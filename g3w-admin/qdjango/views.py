from django.views.generic import (
    CreateView,
    UpdateView,
    ListView,
    DetailView,
    TemplateView,
    FormView,
    View,
)
from django.views.generic.detail import SingleObjectMixin
from django.http import HttpResponseRedirect, HttpResponse
from django.utils.decorators import method_decorator
from django.db import connections
from django.conf import settings
from django.core.cache import caches
from django.utils.translation import gettext_lazy as _
from guardian.decorators import permission_required
from guardian.shortcuts import get_objects_for_user
from core.mixins.views import *
from core.signals import pre_update_project, pre_delete_project, after_update_project, before_delete_project
from core.utils.decorators import check_madd, project_type_permission_required
from django_downloadview import ObjectDownloadView
from rest_framework.response import Response
from usersmanage.mixins.views import G3WACLViewMixin
from usersmanage.models import Group as AuthGroup
from .signals import load_qdjango_widgets_data
from .mixins.views import *
from .forms import *
from .models import TYPE_LAYER_FOR_WIDGET, TYPE_LAYER_FOR_DOWNLOAD
from .api.utils import serialize_vectorjoin
from .utils.models import get_widgets4layer, comparedbdatasource
import json
from collections import OrderedDict


class QdjangoProjectDownloadView(ObjectDownloadView):
    """
    Download Qgis project File
    """
    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectDownloadView, self).dispatch(*args, **kwargs)


class QdjangoProjectListView(G3WRequestViewMixin, G3WGroupViewMixin, ListView):
    template_name = 'qdjango/project_list.html'

    def get_queryset(self):
        return get_objects_for_user(self.request.user, 'qdjango.view_project', Project)\
            .filter(group=self.group).order_by('title')
        # return self.group.qdjango_project.all().order_by('title')

    def get_context_data(self, **kwargs):
        context = super(QdjangoProjectListView,
                        self).get_context_data(**kwargs)
        context['projectPanoramic'] = self.group.project_panoramic.filter(
            project_type='qdjango')

        context['pre_delete_messages'] = {}
        messages = pre_delete_project.send(self, projects=self.object_list)
        for message in messages:
            msg = message[1]
            if msg:
                for m in msg:
                    context['pre_delete_messages'][m['project'].pk] = m['message']
        return context


class QdjangoProjectCreateView(QdjangoProjectCUViewMixin, G3WGroupViewMixin, G3WRequestViewMixin, CreateView):
    """Create group view."""

    model = Project
    form_class = QdjangoProjectForm

    @method_decorator(permission_required('core.add_project_to_group', (Group, 'slug', 'group_slug'), return_403=True))
    @method_decorator(permission_required('qdjango.add_project', return_403=True))
    @method_decorator(check_madd('MPC:XYamtBJA_JgFGmFvEa9x193rnLg', Project))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectCreateView, self).dispatch(*args, **kwargs)


class QdjangoProjectUpdateView(QdjangoProjectCUViewMixin, G3WGroupViewMixin, G3WRequestViewMixin, G3WACLViewMixin,
                               UpdateView):
    """Update project view."""

    model = Project
    form_class = QdjangoProjectForm

    editor_permission = 'change_project'
    editor2_permission = 'view_project'
    viewer_permission = 'view_project'

    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectUpdateView, self).dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(QdjangoProjectUpdateView,
                        self).get_context_data(**kwargs)
        if self.request.method == 'GET':
            context['pre_update_messages'] = []
            if self.object.is_dirty:
                context['pre_update_messages'].append(
                    _('The project has been modified by G3W-Suite after it was uploaded.'))
            messages = pre_update_project.send(
                self, project=self.object, projectType='qdjango')
            for message in messages:
                if message[1]:
                    context['pre_update_messages'].append(message[1])
        return context

    def form_valid(self, form):
        res = super(QdjangoProjectUpdateView, self).form_valid(form)

        # send project update signal
        after_update_project.send(
            self, app_name='qdjango', project=form.instance)

        # clear cache
        if 'qdjango' in settings.CACHES:
            caches['qdjango'].delete(
                settings.QDJANGO_PRJ_CACHE_KEY.format(form.instance.pk))
        return res


class QdjangoProjectFastUpdateView(QdjangoProjectCUViewMixin, G3WGroupViewMixin, G3WRequestViewMixin, View):
    """
    View for fast change project by ajaxfiler
    """

    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectFastUpdateView, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):

        qgis_file = request.FILES['files[]'] if request.FILES else None
        qgskwargs = dict()
        qgskwargs['instance'] = Project.objects.get(slug=kwargs['slug'])
        qgskwargs['group'] = self.group
        qgis_project = QgisProject(qgis_file, **qgskwargs)
        try:
            qgis_project.clean()
        except Exception as e:
            raise ValidationError(e)

        qgis_project.save()

        return HttpResponse('Qgis project uploaded and updated')


class QdjangoProjectDetailView(G3WRequestViewMixin, DetailView):
    """Detail view."""

    model = Project
    template_name = 'qdjango/ajax/project_detail.html'

    @method_decorator(permission_required('qdjango.view_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectDetailView, self).dispatch(*args, **kwargs)


class QdjangoProjectDeleteView(G3WAjaxDeleteViewMixin, SingleObjectMixin, View):
    '''
    Delete Qdjango project Ajax view
    '''
    model = Project

    @method_decorator(permission_required('qdjango.delete_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoProjectDeleteView, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):

        # send before project delete signal
        self.object = self.get_object()
        before_delete_project.send(
            self, app_name='qdjango', project=self.object)

        # clear cache
        if 'qdjango' in settings.CACHES:
            caches['qdjango'].delete(
                settings.QDJANGO_PRJ_CACHE_KEY.format(self.object.pk))

        return super(QdjangoProjectDeleteView, self).post(request, *args, **kwargs)


# For layers
class QdjangoLayersListView(G3WRequestViewMixin, G3WGroupViewMixin, QdjangoProjectViewMixin, ListView):
    template_name = 'qdjango/layers_list.html'

    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'project_slug'),
                                          raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayersListView, self).dispatch(*args, **kwargs)

    def get_queryset(self):
        # get project by project_slug
        return Layer.objects.filter(project__slug=self.project_slug)

    def get_context_data(self, **kwargs):
        """Add current project_slug to context."""
        context = super(QdjangoLayersListView, self).get_context_data(**kwargs)

        # get project object
        project = Project.objects.get(slug=self.project_slug)

        # rebuild layers_tree for bootstrap tree view
        qlayers = self.get_queryset()
        layers = {l.qgs_layer_id: l for l in qlayers}

        layersTree = json.loads(project.layers_tree)
        layersTreeBoostrap = []

        def buildLeaf(layer):
            leaf = {}
            leaf['text'] = layer['name']
            if 'nodes' in layer:
                leaf['nodes'] = []
                for node in layer['nodes']:
                    leaf['nodes'].append(buildLeaf(node))
            return leaf

        for l in layersTree:
            layersTreeBoostrap.append(buildLeaf(l))

        context['project_slug'] = self.project_slug
        context['layers_tree'] = json.dumps(layersTreeBoostrap)

        context['type_layer_for_widget'] = TYPE_LAYER_FOR_WIDGET
        context['type_layer_for_download'] = TYPE_LAYER_FOR_DOWNLOAD

        return context


class QdjangoLayerCacheView(G3WGroupViewMixin, QdjangoProjectViewMixin, View):
    """
    To set cached layer settings
    """

    def get(self, *args, **kwargs):

        # get layer to work
        layer = Layer.objects.get(pk=kwargs['layer_id'])

        if 'cached' in self.request.GET and not bool(int(self.request.GET['cached'])):
            layer.tilestache_conf = None
        else:
            # build tilestache layer configuration
            layer.tilestache_conf = {
                "provider": {
                    "name": layer.name,
                    "template": "{}://{}{}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS={}&STYLES=&FORMAT=image/png&TRANSPARENT=undefined&CRS={}&WIDTH=$width&HEIGHT=$height&bbox=$xmin,$ymin,$xmax,$ymax".format(
                        self.request.META['wsgi.url_scheme'],
                        self.request.META['HTTP_HOST'],
                        reverse('ows', args=[
                            kwargs['group_slug'], 'qdjango', layer.project.id]),
                        layer.name,
                        'EPSG:3857'
                    )
                },
                "projection": "spherical mercator"
            }

        # todo: build new tilestache project object for epsg: 3003, 3004 , etc.
        layer.save()

        return JsonResponse({'Saved': 'ok'})


class QdjangoLayerDataView(G3WGroupViewMixin, QdjangoProjectViewMixin, View):
    """
    By ajax call can change few strict layer model attributes.
    """
    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'project_slug'),
                                          raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerDataView, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        """
        Save params for layer
        """
        layer = Layer.objects.get(pk=kwargs['layer_id'])
        if 'exclude_from_legend' in request.POST:
            layer.exclude_from_legend = int(
                request.POST['exclude_from_legend'])

        if 'not_show_attributes_table' in request.POST:
            layer.not_show_attributes_table = int(
                request.POST['not_show_attributes_table'])

        for format in settings.G3WADMIN_VECTOR_LAYER_DOWNLOAD_FORMATS:
            k = 'download_layer'
            mparam = 'download'

            if format != 'shp':
                suffix = f'_{format}'
                k += suffix
                mparam += suffix

            if k in request.POST:
                setattr(layer, mparam, int(request.POST[k]))

        if 'external' in request.POST:
            layer.external = int(request.POST['external'])
        layer.save()
        return JsonResponse({'Saved': 'ok'})


def project_layers4search_widget(layer):
    """
    Return a dict {layer.qgs_project_id: layer} for Search Widget
    :param layer: Qdjango Model Layer instance
    :return: dict {layer.qgs_project_id: layer}
    :return-type: dict
    """

    return {
        l.qgs_layer_id: (
            lambda l: l.name if l.datasource != layer.datasource else f'{l.name} ({_("same datasource")})')(l)
        for l in layer.project.layer_set.filter(~Q(pk=layer.pk))
    }

class QdjangoLayerWidgetsMixin(object):

    def get_relations(self, layer):
        """
        Get and return  relations from project where layer is a children

        :param layer: Qdjango model Layer instance
        :return: Relations data
        :return type: dict
        """

        toret = []

        if layer.project.relations:
            relations = json.loads(layer.project.relations)
            for relation in relations:
                if relation['referencingLayer'] == layer.qgs_layer_id:
                    toret.append(relation)
        return toret

    def get_context_data(self, **kwargs):
        context = super().get_context_data()
        context['layer'] = self.layer
        context['project_layers'] = json.dumps(project_layers4search_widget(self.layer))

        # Get every relations were layer is child
        context['relations'] = json.dumps(self.get_relations(self.layer))

        load_qdjango_widgets_data.send(self, context=context)

        return context

    def get_success_url(self):
        return None


class QdjangoLayerWidgetsView(G3WGroupViewMixin, QdjangoProjectViewMixin, QdjangoLayerViewMixin, ListView):
    """
    Render layer's widgets list.
    """
    model = Widget
    template_name = 'qdjango/ajax/layer_widgets.html'

    @method_decorator(permission_required('qdjango.view_project', (Project, 'slug', 'project_slug'),
                                          raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerWidgetsView, self).dispatch(*args, **kwargs)

    def get_queryset(self):
        return get_widgets4layer(self.layer)


class QdjangoLayerWidgetCreateView(QdjangoLayerWidgetsMixin, G3WRequestViewMixin, G3WGroupViewMixin, QdjangoProjectViewMixin,
                                   QdjangoLayerViewMixin, AjaxableFormResponseMixin, CreateView):

    form_class = QdjangoWidgetForm
    template_name = 'qdjango/ajax/widget_form.html'

    @method_decorator(permission_required('qdjango.add_widget', return_403=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerWidgetCreateView, self).dispatch(*args, **kwargs)

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.datasource = self.layer.datasource

        # to assign permissions the widget must be committed to DB
        ret = super(QdjangoLayerWidgetCreateView, self).form_valid(form)

        # add layer
        self.object.layers.add(self.layer)

        '''
        if not self.request.user.is_superuser:
            self.object.addPermissionsToEditor(self.request.user)
        else:
            editor_users = get_users_for_object(self.layer, 'change_layer', 'Editor Maps Groups')
            if editor_users:
                self.object.addPermissionsToEditor(editor_users[0])

        viewers = map(lambda o: o.id, get_users_for_object(self.layer, 'view_layer', 'Viewer Maps Groups'))
        self.object.addPermissionsToViewers(viewers)
        '''

        return ret


class QdjangoLayerWidgetUpdateView(QdjangoLayerWidgetsMixin, G3WRequestViewMixin, G3WGroupViewMixin, QdjangoProjectViewMixin,
                                   QdjangoLayerViewMixin, AjaxableFormResponseMixin, UpdateView):

    form_class = QdjangoWidgetForm
    model = Widget
    template_name = 'qdjango/ajax/widget_form.html'

    # @method_decorator(permission_required('qdjango.change_widget', (Widget, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerWidgetUpdateView, self).dispatch(*args, **kwargs)


class QdjangoLayerWidgetDeleteView(G3WAjaxDeleteViewMixin, SingleObjectMixin, View):
    '''
    Delete Qdjango project layer widget Ajax view
    '''
    model = Widget

    # @method_decorator(permission_required('qdjango.delete_widget', (Widget, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerWidgetDeleteView, self).dispatch(*args, **kwargs)


class QdjangoLinkWidget2LayerView(G3WRequestViewMixin, G3WGroupViewMixin, QdjangoProjectViewMixin, QdjangoLayerViewMixin, View):
    """
    Activate or deactivate widget for layer.
    """

    def get(self, *args, **kwargs):
        self.widget = get_object_or_404(Widget, slug=kwargs['slug'])
        try:
            self.linkUnlinkWidget(link=(not 'unlink' in self.request.GET))
            return JsonResponse({'status': 'ok'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'errors_form': e.message})

    def linkUnlinkWidget(self, link=True):

        # apply check datasourc only for postgres and spatialite
        if self.layer.layer_type in ('postgres', 'spatialite') \
                and not comparedbdatasource(self.layer.datasource, self.widget.datasource, self.layer.layer_type):
            raise Exception(
                'Datasource of widget is different from layer datasource')
        if link:
            self.widget.layers.add(self.layer)
        else:
            self.widget.layers.remove(self.layer)


class QdjangoLayerDetailView(G3WRequestViewMixin, DetailView):
    """Detail view for qdjango layer object"""

    model = Layer
    template_name = 'qdjango/ajax/layer_detail.html'

    @method_decorator(permission_required('qdjango.change_project', (Project, 'slug', 'slug'), raise_exception=True))
    def dispatch(self, *args, **kwargs):
        return super(QdjangoLayerDetailView, self).dispatch(*args, **kwargs)


class FilterByUserLayerView(AjaxableFormResponseMixin, G3WProjectViewMixin, G3WRequestViewMixin, FormView):
    """
    View for filter layer by user/group form
    """

    form_class = FitlerByUserLayerForm
    template_name = 'qdjango/layer_actions/fitler_by_user_layer_form.html'

    @method_decorator(project_type_permission_required('change_project', ('project_type', 'project_slug'),
                                                       return_403=True))
    def dispatch(self, request, *args, **kwargs):

        self.layer_id = kwargs['layer_id']
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return None

    def get_form_kwargs(self):

        kwargs = super().get_form_kwargs()

        self.layer = Layer.objects.get(pk=self.layer_id)

        # get viewer users
        viewers = get_viewers_for_object(self.layer, self.request.user, 'view_layer', with_anonymous=True)

        editor_pk = self.layer.project.editor.pk if self.layer.project.editor else None
        self.initial_viewer_users = kwargs['initial']['viewer_users'] = [int(o.id) for o in viewers
                                                                         if o.id != editor_pk]
        group_viewers = get_user_groups_for_object(self.layer, self.request.user, 'view_layer', 'viewer')
        self.initial_viewer_user_groups = kwargs['initial']['user_groups_viewer'] = [o.id for o in group_viewers]

        return kwargs

    def form_valid(self, form):

        # give permission to viewers:
        toAdd = toRemove = None

        currentViewerUsers = [int(i) for i in form.cleaned_data['viewer_users']]
        toRemove = list(set(self.initial_viewer_users) - set(currentViewerUsers))
        toAdd = list(set(currentViewerUsers) - set(self.initial_viewer_users))

        if toAdd:
            for uid in toAdd:
                setPermissionUserObject(User.objects.get(pk=uid), self.layer, ['view_layer'])

        if toRemove:
            for uid in toRemove:
                setPermissionUserObject(User.objects.get(pk=uid), self.layer, ['view_layer'], mode='remove')

        # give permission to user groups viewers:
        to_add = to_remove = None

        current_user_groups_viewers = [int(i) for i in form.cleaned_data['user_groups_viewer']]
        to_remove = list(set(self.initial_viewer_user_groups) - set(current_user_groups_viewers))
        to_add = list(set(current_user_groups_viewers) - set(self.initial_viewer_user_groups))


        if to_add:
            for aid in to_add:
                setPermissionUserObject(AuthGroup.objects.get(pk=aid), self.layer, ['view_layer'])

        if to_remove:
            for aid in to_remove:
                setPermissionUserObject(AuthGroup.objects.get(pk=aid), self.layer, ['view_layer'], mode='remove')

        return super().form_valid(form)