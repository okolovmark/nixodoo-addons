Shows Odoo's own refresh notice, *The page appears to be out of date.* with
its **Refresh** button, in every open backend tab whose code is out of date
after a module update.

Odoo sends ``bundle_changed`` on the bus whenever it saves a tracked asset
bundle (``web.assets_common``, ``web.assets_backend``), but its web client
shows the notice only when the server version changes, which a module update
never does. Tabs opened before a deploy would keep running the old
JavaScript, calling routes that may be gone, until they are reloaded.

How a tab decides:

* On ``bundle_changed`` with an unchanged server version, it waits 10 to 60
  seconds, the random spread of Odoo's own notice, then asks
  ``/web_bundle_refresh_notice/versions`` for the current version of each
  tracked bundle. A further ``bundle_changed`` restarts the wait, so a burst
  of them brings one request.
* It compares them with the versions of the tracked bundles it loaded, read
  from the ``data-asset-version`` of their ``script`` and ``link`` elements.
  One differs: the notice appears. ``debug=assets`` tabs are compared too.
* The route computes the versions through the page's own asset path, so a
  session with a website forced in it gets the versions of that website's
  bundle.
* One notice at a time per tab; once it is closed, a later change shows it
  again. A change of server version is left to Odoo's own notice. The page
  is never reloaded without the user.

The notice runs wherever ``web.assets_backend`` is loaded, the same pages as
Odoo's own notice: the web client, Point of Sale and the subcontracting
portal. The website and the portal do not get it, nor do tabs opened before
the module was installed.
