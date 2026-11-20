After a deploy, an open backend tab still running the old code shows
*The page appears to be out of date.* 10 to 60 seconds after Odoo has
rebuilt its assets:

* press **Refresh** to reload the page on the new code;
* or close the notice, keep working and reload later. Until then the tab
  runs the old code, which may call routes the update removed.

Tabs opened after the deploy already run the new code and show nothing.
