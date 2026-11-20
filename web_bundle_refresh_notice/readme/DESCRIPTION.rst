Shows Odoo's own refresh notice, *The page appears to be out of date.* with
its **Refresh** button, in every open backend tab whose code is out of date
after a module update.

Odoo sends ``bundle_changed`` on the bus whenever it saves its tracked asset
bundle, ``web.assets_web``, but its web client shows the notice only when the
server version changes, which a module update never does. Tabs opened before
a deploy would keep running the old JavaScript, calling routes that may be
gone, until they are reloaded.

How a tab decides:

* When it starts, it asks ``/web_bundle_refresh_notice/versions`` for the
  files of ``web.assets_web`` and their versions, as the web client's asset
  URLs carry them (Odoo versions the script and the stylesheet apart), and
  keeps the answer.
* On ``bundle_changed`` with an unchanged server version, it waits 10 to 60
  seconds, the random spread of Odoo's own notice, asks again and compares:
  a file in another version, a new file or a gone file brings the notice. A
  further ``bundle_changed`` restarts the wait, so a burst of them brings
  one request.
* The route answers the same whatever the session's debug mode or website,
  so ``debug=assets`` tabs, dark mode tabs and the website editor are
  compared too.
* The route skips the identity check, so a session awaiting one gets no lock
  dialog for it: its answer is asset checksums, which page URLs carry
  anyway.
* One notice at a time per tab; once it is closed, a later change shows it
  again. A change of server version is left to Odoo's own notice. The page
  is never reloaded without the user.

The notice runs wherever ``web.assets_backend`` is loaded, the same pages as
Odoo's own notice: the backend web client. The website, the portal, Point of
Sale and the subcontracting portal load other bundles and do not get it, nor
do tabs opened before the module was installed.
