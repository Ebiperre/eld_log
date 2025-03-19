from datetime import datetime, timedelta

class HOSCalculator:
    """Utility class for calculating Hours of Service constraints"""
    
    def __init__(self, current_hours_used=0):
        self.current_hours_used = current_hours_used
        self.driving_hours_left = 11 - current_hours_used  # 11-hour driving limit
        self.duty_window_left = 14 - current_hours_used    # 14-hour window
        self.cycle_hours_left = 70 - current_hours_used    # 70-hour/8-day limit
        self.time_since_last_break = 0  # Time since last 30-minute break
    
    def can_drive(self, hours):
        """Check if driver can legally drive for the specified hours"""
        if hours <= 0:
            return True
            
        if self.driving_hours_left <= 0 or self.duty_window_left <= 0:
            return False
            
        # Check if a 30-minute break is needed
        if self.time_since_last_break >= 8:
            return False
            
        return hours <= self.driving_hours_left
    
    def add_driving_time(self, hours):
        """Add driving time and update HOS status"""
        if not self.can_drive(hours):
            raise ValueError("Cannot drive for the specified hours under HOS regulations")
            
        self.driving_hours_left -= hours
        self.duty_window_left -= hours
        self.cycle_hours_left -= hours
        self.time_since_last_break += hours
        
        return {
            'driving_hours_left': self.driving_hours_left,
            'duty_window_left': self.duty_window_left,
            'cycle_hours_left': self.cycle_hours_left,
            'break_needed': self.time_since_last_break >= 8
        }
    
    def add_on_duty_time(self, hours):
        """Add on-duty (not driving) time and update HOS status"""
        if self.duty_window_left <= 0:
            raise ValueError("14-hour duty window expired")
            
        self.duty_window_left -= hours
        self.cycle_hours_left -= hours
        
        return {
            'driving_hours_left': self.driving_hours_left,
            'duty_window_left': self.duty_window_left,
            'cycle_hours_left': self.cycle_hours_left
        }
    
    def take_break(self, hours):
        """Take a break and update HOS status"""
        # If break is 30+ minutes, reset the break timer
        if hours >= 0.5:
            self.time_since_last_break = 0
        
        # If break is 10+ hours, reset driving and duty window
        if hours >= 10:
            self.driving_hours_left = 11
            self.duty_window_left = 14
        
        return {
            'driving_hours_left': self.driving_hours_left,
            'duty_window_left': self.duty_window_left,
            'cycle_hours_left': self.cycle_hours_left,
            'time_since_last_break': self.time_since_last_break
        }
    
    def plan_route(self, segments):
        """
        Plan a route with HOS regulations in mind
        segments: list of dict with distance_miles and segment_type
        """
        planned_segments = []
        current_segment = 0
        
        while current_segment < len(segments):
            segment = segments[current_segment]
            
            if segment['segment_type'] == 'driving':
                drive_hours = segment['distance_miles'] / 60  # Assuming 60 mph
                
                # Check if we can drive this segment
                if self.can_drive(drive_hours):
                    # Add the segment as-is
                    planned_segments.append(segment)
                    self.add_driving_time(drive_hours)
                    current_segment += 1
                else:
                    # Check if we need a 30-minute break
                    if self.time_since_last_break >= 8 and self.driving_hours_left > 0:
                        planned_segments.append({
                            'segment_type': 'break',
                            'distance_miles': 0,
                            'estimated_drive_time': 0.5,
                            'location': segment['start_location']
                        })
                        self.take_break(0.5)
                    else:
                        # Need a 10-hour break
                        planned_segments.append({
                            'segment_type': 'rest',
                            'distance_miles': 0,
                            'estimated_drive_time': 10,
                            'location': segment['start_location']
                        })
                        self.take_break(10)
            else:
                # Non-driving segment
                if segment['segment_type'] in ['rest', 'break']:
                    self.take_break(segment['estimated_drive_time'])
                else:
                    self.add_on_duty_time(segment['estimated_drive_time'])
                
                planned_segments.append(segment)
                current_segment += 1
                
        return planned_segments