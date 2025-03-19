from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Trip, RouteSegment, LogEntry
from .serializers import TripSerializer, RouteSegmentSerializer, LogEntrySerializer
from datetime import datetime, timedelta
from django.shortcuts import get_object_or_404
import geopy.distance
import math

class TripViewSet(viewsets.ModelViewSet):
    queryset = Trip.objects.all()
    serializer_class = TripSerializer
    
    @action(detail=True, methods=['post'])
    def plan_route(self, request, pk=None):
        trip = self.get_object()
        
        # Clear any existing segments and logs for this trip
        RouteSegment.objects.filter(trip=trip).delete()
        LogEntry.objects.filter(trip=trip).delete()
        
        # Initialize geocoder and get locations
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="trucking_app")
        
        try:
            # Geocode locations and calculate distances
            current_location = geolocator.geocode(trip.current_location)
            pickup_location = geolocator.geocode(trip.pickup_location)
            dropoff_location = geolocator.geocode(trip.dropoff_location)
            
            if not all([current_location, pickup_location, dropoff_location]):
                return Response({"error": "Could not geocode one or more locations"}, status=400)
                
            # Calculate distances
            current_to_pickup = geopy.distance.distance(
                (current_location.latitude, current_location.longitude),
                (pickup_location.latitude, pickup_location.longitude)
            ).miles
            
            pickup_to_dropoff = geopy.distance.distance(
                (pickup_location.latitude, pickup_location.longitude),
                (dropoff_location.latitude, dropoff_location.longitude)
            ).miles
            
            # Create initial segments (we'll process them to add breaks)
            initial_segments = []
            
            # First segment: driving to pickup
            driving_time_to_pickup = current_to_pickup / 60  # Assuming 60 mph
            initial_segments.append({
                'type': 'driving',
                'start_location': trip.current_location,
                'end_location': trip.pickup_location,
                'distance_miles': current_to_pickup,
                'estimated_drive_time': driving_time_to_pickup
            })
            
            # Pickup stop (1 hour)
            initial_segments.append({
                'type': 'pickup',
                'start_location': trip.pickup_location,
                'end_location': trip.pickup_location,
                'distance_miles': 0,
                'estimated_drive_time': 1
            })
            
            # Driving from pickup to dropoff
            driving_time_to_dropoff = pickup_to_dropoff / 60  # Assuming 60 mph
            initial_segments.append({
                'type': 'driving',
                'start_location': trip.pickup_location,
                'end_location': trip.dropoff_location,
                'distance_miles': pickup_to_dropoff,
                'estimated_drive_time': driving_time_to_dropoff
            })
            
            # Dropoff stop (1 hour)
            initial_segments.append({
                'type': 'dropoff',
                'start_location': trip.dropoff_location,
                'end_location': trip.dropoff_location,
                'distance_miles': 0,
                'estimated_drive_time': 1
            })
            
            # Add fuel stops if needed
            initial_segments = self.add_fuel_stops(initial_segments)
            
            # Process segments to add HOS-compliant breaks
            processed_segments = self.apply_hos_regulations(initial_segments, trip.current_hours_used)
            
            # Save segments to database
            for segment in processed_segments:
                RouteSegment.objects.create(
                    trip=trip,
                    start_location=segment['start_location'],
                    end_location=segment['end_location'],
                    distance_miles=segment['distance_miles'],
                    estimated_drive_time=segment['estimated_drive_time'],
                    segment_type=segment['type']
                )
            
            # Generate ELD logs
            self.generate_logs(trip)
            
            return Response({"status": "Route planned successfully"})
        
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    def add_fuel_stops(self, segments):
        """Add fuel stops every 1000 miles on driving segments"""
        result_segments = []
        
        for segment in segments:
            if segment['type'] == 'driving' and segment['distance_miles'] > 1000:
                # Split into segments with fuel stops
                remaining_distance = segment['distance_miles']
                current_location = segment['start_location']
                
                while remaining_distance > 1000:
                    # Add a 1000-mile driving segment
                    fuel_stop_location = f"Fuel Stop ({current_location} to {segment['end_location']})"
                    result_segments.append({
                        'type': 'driving',
                        'start_location': current_location,
                        'end_location': fuel_stop_location,
                        'distance_miles': 1000,
                        'estimated_drive_time': 1000 / 60  # Assuming 60 mph
                    })
                    
                    # Add a fuel stop (30 minutes)
                    result_segments.append({
                        'type': 'fuel',
                        'start_location': fuel_stop_location,
                        'end_location': fuel_stop_location,
                        'distance_miles': 0,
                        'estimated_drive_time': 0.5
                    })
                    
                    current_location = fuel_stop_location
                    remaining_distance -= 1000
                
                # Add the final segment
                if remaining_distance > 0:
                    result_segments.append({
                        'type': 'driving',
                        'start_location': current_location,
                        'end_location': segment['end_location'],
                        'distance_miles': remaining_distance,
                        'estimated_drive_time': remaining_distance / 60  # Assuming 60 mph
                    })
            else:
                result_segments.append(segment)
        
        return result_segments

    def apply_hos_regulations(self, segments, current_hours_used):
        """Apply HOS regulations to insert required breaks"""
        result_segments = []
        
        # Initialize HOS state
        hours_driving = 0  # Since last 10-hour break
        hours_duty = 0     # Since last 10-hour break
        cycle_hours = current_hours_used  # 70-hour/8-day limit
        hours_since_break = 0  # Since last 30-minute break
        
        segment_queue = segments.copy()
        
        while segment_queue:
            segment = segment_queue.pop(0)
            
            if segment['type'] == 'driving':
                # Check if need a 30-minute break before more driving
                if hours_since_break >= 8:
                    # Insert a 30-minute break
                    break_location = segment['start_location']
                    result_segments.append({
                        'type': 'break',
                        'start_location': break_location,
                        'end_location': break_location,
                        'distance_miles': 0,
                        'estimated_drive_time': 0.5
                    })
                    
                    # Reset break timer
                    hours_since_break = 0
                    hours_duty += 0.5
                    cycle_hours += 0.5
                    
                    # Continue with the same segment after the break
                    segment_queue.insert(0, segment)
                    continue
                
                # Check if have enough driving hours left within 11-hour limit
                remaining_drive_time = 11 - hours_driving
                
                if segment['estimated_drive_time'] > remaining_drive_time:
                    # Need to split the driving segment
                    
                    # Drive up to the limit
                    partial_distance = (remaining_drive_time / segment['estimated_drive_time']) * segment['distance_miles']
                    partial_location = f"Rest Stop ({segment['start_location']} to {segment['end_location']})"
                    
                    result_segments.append({
                        'type': 'driving',
                        'start_location': segment['start_location'],
                        'end_location': partial_location,
                        'distance_miles': partial_distance,
                        'estimated_drive_time': remaining_drive_time
                    })
                    
                    # Update HOS state
                    hours_driving += remaining_drive_time
                    hours_duty += remaining_drive_time
                    cycle_hours += remaining_drive_time
                    hours_since_break += remaining_drive_time
                    
                    # Insert a 10-hour rest period
                    result_segments.append({
                        'type': 'rest',
                        'start_location': partial_location,
                        'end_location': partial_location,
                        'distance_miles': 0,
                        'estimated_drive_time': 10
                    })
                    
                    # Reset driving and duty timers
                    hours_driving = 0
                    hours_duty = 0
                    hours_since_break = 0
                    
                    # Add the remainder of the segment back to the queue
                    remaining_distance = segment['distance_miles'] - partial_distance
                    remaining_time = segment['estimated_drive_time'] - remaining_drive_time
                    
                    segment_queue.insert(0, {
                        'type': 'driving',
                        'start_location': partial_location,
                        'end_location': segment['end_location'],
                        'distance_miles': remaining_distance,
                        'estimated_drive_time': remaining_time
                    })
                    
                    continue
                
                # Check if have enough time in the 14-hour window
                if hours_duty + segment['estimated_drive_time'] > 14:
                    # Need to take a 10-hour break to reset the window
                    break_location = segment['start_location']
                    
                    result_segments.append({
                        'type': 'rest',
                        'start_location': break_location,
                        'end_location': break_location,
                        'distance_miles': 0,
                        'estimated_drive_time': 10
                    })
                    
                    # Reset driving and duty timers
                    hours_driving = 0
                    hours_duty = 0
                    hours_since_break = 0
                    
                    # Continue with the same segment after the break
                    segment_queue.insert(0, segment)
                    continue
                
                # Can drive this segment
                result_segments.append(segment)
                
                # Update HOS state
                hours_driving += segment['estimated_drive_time']
                hours_duty += segment['estimated_drive_time']
                cycle_hours += segment['estimated_drive_time']
                hours_since_break += segment['estimated_drive_time']
                
            elif segment['type'] in ['rest', 'break']:
                # Rest periods
                result_segments.append(segment)
                
                if segment['estimated_drive_time'] >= 10:
                    # 10+ hour break resets driving and duty windows
                    hours_driving = 0
                    hours_duty = 0
                    hours_since_break = 0
                elif segment['estimated_drive_time'] >= 0.5:
                    # 30+ minute break resets break timer
                    hours_since_break = 0
                    hours_duty += segment['estimated_drive_time']
                    cycle_hours += segment['estimated_drive_time']
                else:
                    # Shorter breaks just count as duty time
                    hours_duty += segment['estimated_drive_time']
                    cycle_hours += segment['estimated_drive_time']
                    
            else:
                # Other on-duty activities (pickup, dropoff, fuel)
                result_segments.append(segment)
                
                # Update HOS state
                hours_duty += segment['estimated_drive_time']
                cycle_hours += segment['estimated_drive_time']
        
        return result_segments
    
    def generate_logs(self, trip):
        """Generate ELD logs based on the route segments"""
        segments = RouteSegment.objects.filter(trip=trip).order_by('id')
        
        # Start with the current date and time
        current_datetime = datetime.now()
        # Round to the nearest hour to make logs cleaner
        current_datetime = current_datetime.replace(minute=0, second=0, microsecond=0)
        
        for segment in segments:
            status = self.segment_type_to_status(segment.segment_type)
            
            # Calculate end datetime based on segment duration
            start_datetime = current_datetime
            end_datetime = start_datetime + timedelta(hours=segment.estimated_drive_time)
            
            # If segment doesn't cross midnight, create a single log entry
            if start_datetime.date() == end_datetime.date():
                LogEntry.objects.create(
                    trip=trip,
                    date=start_datetime.date(),
                    status=status,
                    start_time=start_datetime.time(),
                    end_time=end_datetime.time(),
                    location=segment.start_location
                )
            else:
                # Create first day's entry (from start time to 23:59:59)
                midnight = datetime.combine(start_datetime.date() + timedelta(days=1), datetime.min.time())
                midnight_minus_1sec = midnight - timedelta(seconds=1)
                
                LogEntry.objects.create(
                    trip=trip,
                    date=start_datetime.date(),
                    status=status,
                    start_time=start_datetime.time(),
                    end_time=midnight_minus_1sec.time(),
                    location=segment.start_location
                )
                
                # If segment spans multiple days, create entries for each full day
                current_date = start_datetime.date() + timedelta(days=1)
                remaining_days = (end_datetime.date() - current_date).days
                
                # For each full day in between (if any)
                for _ in range(remaining_days):
                    next_midnight = datetime.combine(current_date + timedelta(days=1), datetime.min.time())
                    next_midnight_minus_1sec = next_midnight - timedelta(seconds=1)
                    
                    LogEntry.objects.create(
                        trip=trip,
                        date=current_date,
                        status=status,
                        start_time=datetime.min.time(),
                        end_time=next_midnight_minus_1sec.time(),
                        location=segment.start_location
                    )
                    current_date += timedelta(days=1)
                
                # Create final day's entry (from 00:00:00 to end time)
                LogEntry.objects.create(
                    trip=trip,
                    date=end_datetime.date(),
                    status=status,
                    start_time=datetime.min.time(),
                    end_time=end_datetime.time(),
                    location=segment.start_location
                )
            
            # Update current datetime for next segment
            current_datetime = end_datetime
    
    def segment_type_to_status(self, segment_type):
        """Map segment types to ELD status codes"""
        mapping = {
            'driving': 'driving',
            'rest': 'off_duty',
            'pickup': 'on_duty_not_driving',
            'dropoff': 'on_duty_not_driving',
            'fuel': 'on_duty_not_driving',
            'break': 'off_duty'
        }
        return mapping.get(segment_type, 'on_duty_not_driving')
    
    @action(detail=True, methods=['get'])
    def segments(self, request, pk=None):
        """Return all route segments for a trip"""
        trip = self.get_object()
        segments = RouteSegment.objects.filter(trip=trip).order_by('id')
        serializer = RouteSegmentSerializer(segments, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        """Return all ELD logs for a trip"""
        trip = self.get_object()
        logs = LogEntry.objects.filter(trip=trip).order_by('date', 'start_time')
        serializer = LogEntrySerializer(logs, many=True)
        return Response(serializer.data)