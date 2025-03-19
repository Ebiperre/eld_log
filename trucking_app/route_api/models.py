from django.db import models

class Trip(models.Model):
    current_location = models.CharField(max_length=255)
    pickup_location = models.CharField(max_length=255)
    dropoff_location = models.CharField(max_length=255)
    current_hours_used = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Trip from {self.current_location} to {self.dropoff_location}"

class RouteSegment(models.Model):
    trip = models.ForeignKey(Trip, related_name='segments', on_delete=models.CASCADE)
    start_location = models.CharField(max_length=255)
    end_location = models.CharField(max_length=255)
    distance_miles = models.FloatField()
    estimated_drive_time = models.FloatField()  # In hours
    segment_type = models.CharField(max_length=50)  # driving, rest, pickup, dropoff, fuel
    
    def __str__(self):
        return f"{self.segment_type}: {self.start_location} to {self.end_location}"

class LogEntry(models.Model):
    trip = models.ForeignKey(Trip, related_name='logs', on_delete=models.CASCADE)
    date = models.DateField()
    status = models.CharField(max_length=50)  # driving, on_duty_not_driving, off_duty, sleeper_berth
    start_time = models.TimeField()
    end_time = models.TimeField()
    location = models.CharField(max_length=255)
    
    def __str__(self):
        return f"{self.date}: {self.status} from {self.start_time} to {self.end_time}"