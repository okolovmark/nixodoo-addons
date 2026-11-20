* A change that touches only bundles Odoo does not track brings no notice.
  Point of Sale, for one, loads the backend code through a bundle of its
  own, so there only a change to ``web.assets_common`` brings it.
* The test of a website forced in the session runs only in a database with
  ``website`` installed.
