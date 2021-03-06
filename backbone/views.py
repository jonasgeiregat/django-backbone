import json

from django.core.serializers.json import DjangoJSONEncoder
from django.core.urlresolvers import reverse
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.forms.models import modelform_factory
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.translation import ugettext as _
from django.views.generic import View

from backbone.serializers import AllFieldsSerializer


class BackboneAPIView(View):
    model = None  # The model to be used for this API definition
    display_fields = []  # Fields to return for read (GET) requests,
    fields = []  # Fields to allow when adding (POST) or editing (PUT) objects.
    form = None  # The form class to be used for adding or editing objects.
    ordering = None  # Ordering used when retrieving the collection
    paginate_by = None  # The max number of objects per page (enables use of the ``page`` GET parameter).

    def queryset(self, request, **kwargs):
        """
        Returns the queryset (along with ordering) to be used when retrieving object(s).
        """
        qs = self.model._default_manager.all()
        if self.ordering:
            qs = qs.order_by(*self.ordering)
        return qs

    def get(self, request, id=None, **kwargs):
        """
        Handles get requests for either the collection or an object detail.
        """
        if id:
            obj = get_object_or_404(self.queryset(request, **kwargs), id=id)
            return self.get_object_detail(request, obj)
        else:
            return self.get_collection(request, **kwargs)

    def get_object_detail(self, request, obj):
        """
        Handles get requests for the details of the given object.
        """
        data = self.serialize(obj, ['id'] + list(self.display_fields))
        return HttpResponse(self.json_dumps(data), content_type='application/json')

    def get_collection(self, request, **kwargs):
        """
        Handles get requests for the list of objects.
        """
        qs = self.queryset(request, **kwargs)

        if self.paginate_by is not None:
            page = request.GET.get('page', 1)
            paginator = Paginator(qs, self.paginate_by)
            try:
                qs = paginator.page(page).object_list
            except PageNotAnInteger:
                data = _('Invalid `page` parameter: Not a valid integer.')
                return HttpResponseBadRequest(data)
            except EmptyPage:
                data = _('Invalid `page` parameter: Out of range.')
                return HttpResponseBadRequest(data)
        data = [
            self.serialize(obj, ['id'] + list(self.display_fields)) for obj in qs
        ]
        return HttpResponse(self.json_dumps(data), content_type='application/json')

    def post(self, request, id=None, **kwargs):
        """
        Handles post requests.
        """
        if id:
            # No posting to an object detail page
            return HttpResponseForbidden()
        else:
            if not self.has_add_permission(request):
                return HttpResponseForbidden(_('You do not have permission to perform this action.'))
            else:
                return self.add_object(request)

    def add_object(self, request):
        """
        Adds an object.
        """
        try:
            # backbone sends data in the body in json format
            # Conditional statement is for backwards compatibility with Django <= 1.3
            data = json.loads(request.body if hasattr(request, 'body') else request.raw_post_data)
        except ValueError:
            return HttpResponseBadRequest(_('Unable to parse JSON request body.'))

        form = self.get_form_instance(request, data=data)
        if form.is_valid():
            if not self.has_add_permission_for_data(request, form.cleaned_data):
                return HttpResponseForbidden(_('You do not have permission to perform this action.'))

            obj = form.save()

            # We return the newly created object's details and a Location header with it's url
            response = self.get_object_detail(request, obj)
            response.status_code = 201

            url_name = 'backbone:%s_%s_detail' % (self.model._meta.app_label, self.model._meta.module_name)
            response['Location'] = reverse(url_name, args=[obj.id])
            return response
        else:
            return HttpResponseBadRequest(self.json_dumps(form.errors), content_type='application/json')

    def put(self, request, id=None, **kwargs):
        """
        Handles put requests.
        """
        if id:
            obj = get_object_or_404(self.queryset(request), id=id)
            if not self.has_update_permission(request, obj):
                return HttpResponseForbidden(_('You do not have permission to perform this action.'))
            else:
                return self.update_object(request, obj)
        else:
            # No putting on a collection.
            return HttpResponseForbidden()

    def update_object(self, request, obj):
        """
        Updates an object.
        """
        try:
            # backbone sends data in the body in json format
                # Conditional statement is for backwards compatibility with Django <= 1.3
            data = json.loads(request.body if hasattr(request, 'body') else request.raw_post_data)
        except ValueError:
            return HttpResponseBadRequest(_('Unable to parse JSON request body.'))

        form = self.get_form_instance(request, data=data, instance=obj)
        if form.is_valid():
            if not self.has_update_permission_for_data(request, form.cleaned_data):
                return HttpResponseForbidden(_('You do not have permission to perform this action.'))
            form.save()

            # We return the updated object details
            return self.get_object_detail(request, obj)
        else:
            return HttpResponseBadRequest(self.json_dumps(form.errors), content_type='application/json')

    def get_form_instance(self, request, data=None, instance=None):
        """
        Returns an instantiated form to be used for adding or editing an object.

        The `instance` argument is the model instance (passed only if this form
        is going to be used for editing an existing object).
        """
        defaults = {}
        if self.form:
            defaults['form'] = self.form
        if self.fields:
            defaults['fields'] = self.fields
        return modelform_factory(self.model, **defaults)(data=data, instance=instance)

    def delete(self, request, id=None):
        """
        Handles delete requests.
        """
        if id:
            obj = get_object_or_404(self.queryset(request), id=id)
            if not self.has_delete_permission(request, obj):
                return HttpResponseForbidden(_('You do not have permission to perform this action.'))
            else:
                return self.delete_object(request, obj)
        else:
            # No delete requests allowed on collection view
            return HttpResponseForbidden()

    def delete_object(self, request, obj):
        """
        Deletes the the given object.
        """
        obj.delete()
        return HttpResponse(status=204)

    def has_add_permission(self, request):
        """
        Returns True if the requesting user is allowed to add an object, False otherwise.
        """
        perm_string = '%s.add_%s' % (self.model._meta.app_label,
            self.model._meta.object_name.lower()
        )
        return request.user.has_perm(perm_string)

    def has_add_permission_for_data(self, request, cleaned_data):
        """
        Returns True if the requesting user is allowed to add an object with the
        given data, False otherwise.

        If the add permission does not depend on the data being submitted,
        use `has_add_permission` instead.
        """
        return True

    def has_update_permission(self, request, obj):
        """
        Returns True if the requesting user is allowed to update the given object, False otherwise.
        """
        perm_string = '%s.change_%s' % (self.model._meta.app_label,
            self.model._meta.object_name.lower()
        )
        return request.user.has_perm(perm_string)

    def has_update_permission_for_data(self, request, cleaned_data):
        """
        Returns True if the requesting user is allowed to update the object with the
        given data, False otherwise.

        If the update permission does not depend on the data being submitted,
        use `has_update_permission` instead.
        """
        return True

    def has_delete_permission(self, request, obj):
        """
        Returns True if the requesting user is allowed to delete the given object, False otherwise.
        """
        perm_string = '%s.delete_%s' % (self.model._meta.app_label,
            self.model._meta.object_name.lower()
        )
        return request.user.has_perm(perm_string)

    def serialize(self, obj, fields):
        """
        Serializes a single model instance to a Python object, based on the specified list of fields.
        """

        data = {}
        remaining_fields = []
        for field in fields:
            if callable(field):  # Callable
                data[field.__name__] = field(obj)
            elif hasattr(self, field):  # Method on the view
                data[field] = getattr(self, field)(obj)
            elif hasattr(obj, field):  # Callable/property/field on the model
                attr = getattr(obj, field)
                if callable(attr):  # Callable on the model
                    data[field] = attr()
                else:
                    remaining_fields.append(field)
            else:
                raise AttributeError('Invalid field: %s' % field)

        # Add on db fields
        serializer = AllFieldsSerializer()
        serializer.serialize([obj], fields=list(remaining_fields))
        data.update(serializer.getvalue()[0]['fields'])

        # Any remaining fields should be properties on the model
        remaining_fields = set(remaining_fields) - set(data.keys())

        for field in remaining_fields:
            data[field] = getattr(obj, field)

        return data

    def json_dumps(self, data, **options):
        """
        Wrapper around `json.dumps` that uses a special JSON encoder.
        """
        params = {'sort_keys': True, 'indent': 2}
        params.update(options)
        # This code is based off django's built in JSON serializer
        if json.__version__.split('.') >= ['2', '1', '3']:
            # Use JS strings to represent Python Decimal instances (ticket #16850)
            params.update({'use_decimal': False})
        return json.dumps(data, cls=DjangoJSONEncoder, **params)
