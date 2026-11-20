* A tab takes the versions it compares with from a request when it starts.
  If that request fails, during the restart itself for one, the tab stays
  quiet until it is reloaded; a deploy that lands between the page render
  and that request goes unnoticed in that tab. Taking these versions at
  render time, in ``session_info``, would close both.
* A change that touches only a bundle Odoo does not track brings no notice:
  a dark mode stylesheet alone (``web.assets_web_dark``), for one.
* The website editor test runs only in a database with ``website``
  installed.
