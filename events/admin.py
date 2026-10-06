"""Editorial controls and import diagnostics for the event catalogue."""

from django.contrib import admin

from .models import Category, City, Event, ImportRun, Source, SourceListing


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "timezone", "currency", "is_active")
    list_filter = ("is_active", "country_code")
    search_fields = ("name", "slug", "country_code")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


class SourceListingInline(admin.TabularInline):
    model = SourceListing
    fields = ("source", "external_id", "source_url", "last_seen_at")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "city", "start_date", "start_time", "status", "price_status", "is_published")
    list_filter = ("city", "status", "is_published", "price_status", "categories")
    search_fields = ("title", "description", "venue_name", "address")
    date_hierarchy = "start_date"
    autocomplete_fields = ("city", "categories")
    readonly_fields = ("public_id", "created_at", "updated_at")
    list_select_related = ("city",)
    inlines = (SourceListingInline,)
    fieldsets = (
        (None, {"fields": ("title", "city", "description", "categories", "status", "is_published")}),
        ("Date and time (city local time)", {"fields": ("start_date", "start_time", "end_date", "end_time")}),
        ("Location", {"fields": ("venue_name", "address")}),
        ("Price", {"fields": ("price_status", "price_amount", "currency")}),
        ("Links", {"fields": ("url", "image_url")}),
        ("Record", {"fields": ("public_id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "connector", "enabled", "interval_minutes", "last_success_at")
    list_filter = ("city", "connector", "enabled")
    search_fields = ("name", "slug", "url")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("city",)
    readonly_fields = ("last_success_at",)
    list_select_related = ("city",)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj and obj.pk and obj.listings.exists():
            return (*fields, "city")
        return fields


@admin.register(SourceListing)
class SourceListingAdmin(admin.ModelAdmin):
    list_display = ("external_id", "source", "event", "last_seen_at")
    list_filter = ("source__city", "source")
    search_fields = ("external_id", "source_url", "event__title")
    autocomplete_fields = ("source", "event")
    readonly_fields = ("last_seen_at", "payload_hash")
    list_select_related = ("source", "source__city", "event")


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "source", "status", "started_at", "finished_at",
        "received_count", "created_count", "updated_count",
    )
    list_filter = ("status", "source__city", "source")
    search_fields = ("source__name", "error")
    readonly_fields = (
        "source", "status", "started_at", "finished_at",
        "received_count", "created_count", "updated_count", "error",
    )
    list_select_related = ("source", "source__city")
    date_hierarchy = "started_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
