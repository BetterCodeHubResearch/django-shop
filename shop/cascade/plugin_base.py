# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from django.db.models import get_model
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.forms import widgets
from django.template.loader import select_template
from django.utils.module_loading import import_by_path
from django.utils.translation import ugettext_lazy as _
from django.utils.safestring import mark_safe
from cms.plugin_pool import plugin_pool
from cms.utils.compat.dj import python_2_unicode_compatible
from cmsplugin_cascade.fields import PartialFormField
from cmsplugin_cascade.plugin_base import CascadePluginBase
from cmsplugin_cascade.link.forms import LinkForm
from cmsplugin_cascade.link.fields import LinkSearchField
from cmsplugin_cascade.link.plugin_base import LinkPluginBase, LinkElementMixin
from cmsplugin_cascade.utils import resolve_dependencies
from shop import settings as shop_settings
from shop.models.product import ProductModel


class ShopPluginBase(CascadePluginBase):
    module = "Shop"
    require_parent = False
    allow_children = False


@python_2_unicode_compatible
class ShopLinkElementMixin(LinkElementMixin):
    def __str__(self):
        return self.plugin_class.get_identifier(self)


class ShopLinkPluginBase(ShopPluginBase):
    """
    Base plugin for arbitrary buttons used during various checkout pages.
    """
    allow_children = False
    fields = (('link_type', 'cms_page',), 'glossary',)
    glossary_field_map = {'link': ('link_type', 'cms_page',)}
    allow_children = False
    parent_classes = []
    require_parent = False

    class Media:
        js = resolve_dependencies('shop/js/admin/shoplinkplugin.js')

    @classmethod
    def get_link(cls, obj):
        link = obj.glossary.get('link', {})
        if link.get('type') == 'cmspage':
            if 'model' in link and 'pk' in link:
                if not hasattr(obj, '_link_model'):
                    Model = get_model(*link['model'].split('.'))
                    try:
                        obj._link_model = Model.objects.get(pk=link['pk'])
                    except Model.DoesNotExist:
                        obj._link_model = None
                if obj._link_model:
                    return obj._link_model.get_absolute_url()
        else:
            # use the link type as special action keyword
            return link.get('type')

    def get_ring_bases(self):
        bases = super(ShopLinkPluginBase, self).get_ring_bases()
        bases.append('LinkPluginBase')
        return bases


class ShopButtonPluginBase(ShopLinkPluginBase):
    """
    Base plugin for arbitrary buttons used during various checkout pages.
    """
    fields = ('link_content', ('link_type', 'cms_page',), 'glossary',)

    class Media:
        css = {'all': ('cascade/css/admin/bootstrap.min.css', 'cascade/css/admin/bootstrap-theme.min.css',)}
        js = resolve_dependencies('shop/js/admin/shoplinkplugin.js')

    @classmethod
    def get_identifier(cls, obj):
        return mark_safe(obj.glossary.get('link_content', ''))


class CatalogLinkForm(LinkForm):
    """
    Alternative implementation of `cmsplugin_cascade.TextLinkForm`, which allows to link onto
    the Product model, using its method ``get_absolute_url``.

    Note: In this form class the field ``product`` is missing. It is added later, when the shop's
    Product knows about its materialized model.
    """
    LINK_TYPE_CHOICES = (('cmspage', _("CMS Page")), ('product', _("Product")),
                         ('exturl', _("External URL")), ('email', _("Mail To")),)

    def clean_product(self):
        if self.cleaned_data.get('link_type') == 'product':
            app_label = self.ProductModel._meta.app_label
            self.cleaned_data['link_data'] = {
                'type': 'product',
                'model': '{0}.{1}'.format(app_label, self.ProductModel.__name__),
                'pk': self.cleaned_data['product'] and self.cleaned_data['product'].pk or None,
            }

    def set_initial_product(self, initial):
        try:
            Model = get_model(*initial['link']['model'].split('.'))
            initial['product'] = Model.objects.get(pk=initial['link']['pk'])
        except (KeyError, ObjectDoesNotExist):
            pass

    @classmethod
    def get_form_class(cls):
        # must add field `product` on the fly, because during the declaration this form class
        # the MaterializedModel of the product is not known yet.
        product = LinkSearchField(required=False, label='',
            queryset=ProductModel.objects.all(),
            search_fields=getattr(ProductModel, 'search_fields'),
            help_text=_("An internal link onto a product from the shop"))
        return type(str('LinkForm'), (cls,), {'ProductModel': ProductModel, 'product': product})


class CatalogLinkPluginBase(LinkPluginBase):
    """
    Modified implementation of ``cmsplugin_cascade.link.LinkPluginBase`` which adds link type
    "Product", to set links onto arbitrary products of this shop.
    """
#     glossary_fields = (
#         PartialFormField('title',
#             widgets.TextInput(),
#             label=_("Title"),
#             help_text=_("Link's Title")
#         ),
#     ) + LinkPluginBase.glossary_fields
    glossary_field_map = {'link': ('link_type', 'cms_page', 'product', 'ext_url', 'mail_to',)}

    class Media:
        js = resolve_dependencies('shop/js/admin/shoplinkplugin.js')


class DialogFormPluginBase(ShopPluginBase):
    """
    Base class for all plugins adding a dialog form to a placeholder field.
    """
    require_parent = True
    parent_classes = ('BootstrapColumnPlugin', 'ProcessStepPlugin',)
    CHOICES = (('form', _("Form dialog")), ('summary', _("Summary")),)
    glossary_fields = (
        PartialFormField('render_type',
            widgets.RadioSelect(choices=CHOICES),
            label=_("Render as"),
            initial='form',
            help_text=_("A dialog can also be rendered as a box containing a read-only summary."),
        ),
    )

    @classmethod
    def register_plugin(cls, plugin):
        """
        Register plugins derived from this class with this function instead of
        `plugin_pool.register_plugin`, so that dialog plugins without a corresponding
        form class are not registered.
        """
        if not issubclass(plugin, cls):
            msg = "Can not register plugin class `{}`, since is does not inherit from `{}`."
            raise ImproperlyConfigured(msg.format(plugin.__name__, cls.__name__))
        if plugin.get_form_class() is None:
            msg = "Can not register plugin class `{}`, since is does not define a `form_class`."
            raise ImproperlyConfigured(msg.format(plugin.__name__))
        plugin_pool.register_plugin(plugin)

    @classmethod
    def get_form_class(cls):
        return getattr(cls, 'form_class', None)

    def __init__(self, *args, **kwargs):
        super(DialogFormPluginBase, self).__init__(*args, **kwargs)
        self.FormClass = import_by_path(self.get_form_class())

    def get_form_data(self, request):
        """
        Returns data to initialize the corresponding dialog form.
        This method must return a dictionary containing either `instance` - a Python object to
        initialize the form class for this plugin, or `initial` - a dictionary containing initial
        form data, or if both are set, values from `initial` override those of `instance`.
        """
        return {}

    def get_render_template(self, context, instance, placeholder):
        template_names = [
            '{0}/checkout/{1}'.format(shop_settings.APP_LABEL, self.template_leaf_name),
            'shop/checkout/{}'.format(self.template_leaf_name),
        ]
        return select_template(template_names)

    def render(self, context, instance, placeholder):
        """
        Return the context to render a DialogFormPlugin
        """
        request = context['request']
        form_data = self.get_form_data(request)
        request._plugin_order = getattr(request, '_plugin_order', 0) + 1
        if not isinstance(form_data.get('initial'), dict):
            form_data['initial'] = {}
        form_data['initial'].update(plugin_id=instance.id, plugin_order=request._plugin_order)
        bound_form = self.FormClass(**form_data)
        context[bound_form.form_name] = bound_form
        return super(DialogFormPluginBase, self).render(context, instance, placeholder)