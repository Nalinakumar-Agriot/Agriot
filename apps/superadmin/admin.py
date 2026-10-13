
from django.contrib import admin
from .models import TaskCategory, TaskPriority

@admin.register(TaskCategory)
class TaskCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active', 'created_at')
    search_fields = ('name', 'description')

@admin.register(TaskPriority)
class TaskPriorityAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active', 'created_at')
    search_fields = ('name', 'description')
