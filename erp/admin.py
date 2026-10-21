from django.contrib import admin
from .models import Customer, Supplier, Product, SalesOrder, SalesOrderLine, JobCard, Unit

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'contact_person', 'phone', 'email', 'customer_type', 'is_active')
    search_fields = ('code', 'name', 'email', 'phone', 'contact_person')
    list_filter = ('is_active', 'customer_type')
    readonly_fields = ('creation_key', 'created_at', 'updated_at')


admin.site.register([Supplier, Product, SalesOrder, SalesOrderLine, JobCard])


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active')
    search_fields = ('code', 'name')
    list_filter = ('is_active',)

    def get_readonly_fields(self, request, obj=None):
        return ('code',) if obj else ()
