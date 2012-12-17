"""
Serialization that uses twikifier to render.

It can render in two ways. If config['twikified.render'] is
True, the default, then rendering will be done serverside
with a nodje.js based socket server. Otherwise rendering will be
delegated to the client.

If config['twikified.serializer'] is True (the default is False)
use this code as a serialization, not just a renderer. Whether
rendering is done by the serialization server side or client side
is controlled by twikified.render.

The socket is at config['twikified.socket'], '/tmp/wst.sock' by
default. Running the server is up to the human installer at this point.

If client side rendering is used, then a bunch of javascript is
expected to be found in config['twikified.container'], defaulting
to '/bags/common/tiddlers/'. The human installer is expected to get the
right tiddlers in the right place (for now).
"""

import logging
import Cookie
import socket

import html5lib
from xml.parsers.expat import ExpatError

from tiddlywebplugins.atom.htmllinks import Serialization as HTMLSerialization

from tiddlyweb.control import determine_bag_from_recipe
from tiddlyweb.store import StoreError
from tiddlyweb.model.bag import Bag
from tiddlyweb.model.policy import PermissionsError
from tiddlyweb.model.recipe import Recipe
from tiddlyweb.model.tiddler import Tiddler
from tiddlyweb.util import renderable
from tiddlyweb.web.util import (escape_attribute_value, html_encode,
        recipe_url, bag_url, get_route_value)

REVISION_RENDERER = 'tiddlywebplugins.wikklytextrender'

SERIALIZERS = {
    'text/html': ['twikified', 'text/html; charset=UTF-8'],
    'default': ['twikified', 'text/html; charset=UTF-8'],
}


def init(config):
    """
    Establish if this plugin is to be used a a serializaiton, a renderer,
    or both (which would be weird, but is possible).
    """
    if config.get('twikified.serializer', False):
        config['serializers'].update(SERIALIZERS)
    if config.get('twikified.render', True):
        config['wikitext.default_renderer'] = 'twikified'


def render(tiddler, environ, seen_titles=None):
    """
    Return tiddler.text as rendered HTML by passing it down a 
    socket to the nodejs based server.js process. Transclusions
    are identified in the returned text and processed recursively.

    If there is a current user, that user is passed along the pipe
    so that private content can be retrieved by nodejs (over HTTP).
    """

    if seen_titles is None:
        seen_titles = []

    parser = html5lib.HTMLParser(
            tree = html5lib.treebuilders.getTreeBuilder("dom"))

    if tiddler.recipe:
        collection = recipe_url(environ, Recipe(tiddler.recipe)) + '/tiddlers'
    else:
        collection = bag_url(environ, Bag(tiddler.bag)) + '/tiddlers'

    try:
        user_cookie = environ['HTTP_COOKIE']
        cookie = Cookie.SimpleCookie()
        cookie.load(user_cookie)
        tiddlyweb_cookie = 'tiddlyweb_user=' + cookie['tiddlyweb_user'].value
    except KeyError:
        tiddlyweb_cookie = ''

    socket_path = environ['tiddlyweb.config'].get('twikified.socket',
            '/tmp/wst.sock')
    twik_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    try:
        twik_socket.connect(socket_path)
    except (socket.error, IOError), exc:
        output = """
<div class='error'>There was a problem rendering this tiddler.
The raw text is given below.</div>
<pre class='wikitext'>%s</pre>
""" % (escape_attribute_value(tiddler.text))
        logging.warn('twikifier socket connect failed: %s', exc)
        twik_socket.shutdown(socket.SHUT_RDWR)
        twik_socket.close()
        return output

    try:
        twik_socket.sendall('%s\x00%s\x00%s\n' % (collection,
            tiddler.text.encode('utf-8', 'replace'),
            tiddlyweb_cookie))
        twik_socket.shutdown(socket.SHUT_WR)

        output = ''
        try:
            while True:
                data = twik_socket.recv(1024)
                if data:
                    output += data
                else:
                    break
        finally:
            twik_socket.shutdown(socket.SHUT_RDWR)
            twik_socket.close()
    except (socket.error, IOError), exc:
        logging.warn('twikifier error during data processing: %s', exc)
        output = """
<div class='error'>There was a problem rendering this tiddler.
The raw text is given below.</div>
<pre class='wikitext'>%s</pre>
""" % (escape_attribute_value(tiddler.text))
        twik_socket.shutdown(socket.SHUT_RDWR)
        twik_socket.close()
        return output

    # process for transclusions
    # make the socket output unicode first
    output = output.decode('utf-8', 'replace')
    try:
        dom = parser.parse('<div>' + output + '</div>')
        spans = dom.getElementsByTagName('span')
        for span in spans:
            for attribute in span.attributes.keys():
                if attribute == 'tiddler':
                    attr = span.attributes[attribute]
                    interior_title = attr.value
                    try:
                        span_class = span.attributes['class'].value
                        if span_class.startswith('@'):
                            interior_bag = span_class[1:] + '_public'
                        else:
                            interior_bag = ''
                    except KeyError:
                        interior_bag = ''
                    title_semaphore = '%s:%s' % (interior_title, interior_bag)
                    if title_semaphore not in seen_titles:
                        seen_titles.append(title_semaphore)
                        interior_tiddler = Tiddler(interior_title)
                        try:
                            store = environ['tiddlyweb.store']
                            if interior_bag:
                                public_bag = store.get(Bag(interior_bag))
                                public_bag.policy.allows(
                                        environ['tiddlyweb.usersign'], 'read')
                                interior_tiddler.bag = interior_bag
                            else:
                                if tiddler.recipe:
                                    interior_tiddler.recipe = tiddler.recipe
                                    recipe = store.get(Recipe(tiddler.recipe))
                                    interior_tiddler.bag = determine_bag_from_recipe(
                                            recipe, interior_tiddler, environ).name
                                else:
                                    interior_tiddler.bag = tiddler.bag
                            interior_tiddler = store.get(interior_tiddler)
                        except (StoreError, PermissionsError):
                            continue
                        if renderable(interior_tiddler, environ):
                            interior_content = render(interior_tiddler, environ,
                                    seen_titles)
                            interior_dom = parser.parse('<div>' + 
                                    interior_content
                                    + '</div>')
                            span.appendChild(interior_dom.getElementsByTagName('div')[0])

        output = dom.getElementsByTagName('div')[0].toxml()
    except ExpatError, exc:
        # If expat couldn't process the output, we need to make it
        # unicode as what came over the socket was utf-8 but expat
        # needs that in the first place.
        logging.warn('got expat error: %s:%s %s', tiddler.bag, tiddler.title, exc)
        output = output.decode('utf-8', 'replace')
    return output


def _render_revision(tiddler, environ):
    """
    Fall back to a simpler renderer to deal with rendering revisions.
    twikifier doesn't currently care about revisions.
    """
    renderer = __import__(REVISION_RENDERER, {}, {}, ['render'])
    return renderer.render(tiddler, environ)


class Serialization(HTMLSerialization):

    def _render(self, tiddler):
        return HTMLSerialization.tiddler_as(self, tiddler)

    def tiddler_as(self, tiddler):
        """
        Send out the bare minimum required to make webtwik know there is
        a tiddler.
        """
        # branch away if we are going to use the render system
        if self.environ['tiddlyweb.config'].get('twikified.render', True):
            return self._render(tiddler)

        common_container = self.environ.get(
                'tiddlyweb.config', {}).get(
                        'twikified.container', '/bags/common/tiddlers/')
        scripts = """
<script src="https://ajax.googleapis.com/ajax/libs/jquery/1.4/jquery.min.js"
    type="text/javascript"></script>
<script>
    $.ajaxSetup({
        beforeSend: function(xhr) {
            xhr.setRequestHeader("X-ControlView",
            "false");
        }
    });
</script>
<script src="%(container)stwikifier" type="text/javascript"></script>
<script src="%(container)stwik" type="text/javascript"></script>
<script src="%(container)swebtwik" type="text/javascript"></script>
""" % {'container': common_container}
        tiddler_div = ('<div class="tiddler" title="%s" %s><pre>%s</pre></div>'
                % (escape_attribute_value(tiddler.title),
                    self._tiddler_provenance(tiddler),
                    self._text(tiddler)))
        self.environ['tiddlyweb.title'] = tiddler.title
        return tiddler_div + scripts

    def _text(self, tiddler):
        if not tiddler.type or tiddler.type == 'None':
            return html_encode(tiddler.text)
        return ''

    def _tiddler_provenance(self, tiddler):
        if tiddler.recipe:
            return 'server.recipe="%s"' % escape_attribute_value(
                    tiddler.recipe)
        else:
            return 'server.bag="%s"' % escape_attribute_value(tiddler.bag)
