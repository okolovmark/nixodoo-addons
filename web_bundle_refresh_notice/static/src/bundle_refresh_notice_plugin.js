import { Plugin, usePlugin } from "@odoo/owl";
import { BusPlugin } from "@bus/services/bus_plugin";
import { browser, location } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { services } from "@web/core/services";
import { shallowEqual } from "@web/core/utils/objects";
import { session } from "@web/session";

const VERSIONS_ROUTE = "/web_bundle_refresh_notice/versions";

/**
 * @param {Object<string, string> | null | undefined} versions `{ file: version }`
 */
function hasVersions(versions) {
    return Boolean(versions && Object.keys(versions).length);
}

/**
 * Odoo's own refresh notice (`AssetsWatchdogPlugin`) for a bundle change that keeps the server
 * version, a module deploy: the watchdog only speaks when the server version differs. The page
 * carries the bundle versions it was rendered with (`session.bundle_versions`), and the tab shows
 * the notice once the server gives others: any file in another version, gone or new.
 */
export class BundleRefreshNoticePlugin extends Plugin {
    busService = usePlugin(BusPlugin);
    notification = usePlugin(NotificationPlugin);

    isNoticeDisplayed = false;
    checkTimeoutId = null;

    setup() {
        // A page rendered before this module was installed carries no baseline: its tab stays quiet.
        this.baselineVersions = session.bundle_versions;
        if (!hasVersions(this.baselineVersions)) {
            return;
        }
        this.busService.subscribe("bundle_changed", ({ server_version }) => {
            if (server_version === session.server_version && !this.isNoticeDisplayed) {
                // The watchdog's random wait, so open tabs do not all ask, nor reload, at once.
                browser.clearTimeout(this.checkTimeoutId);
                this.checkTimeoutId = browser.setTimeout(
                    () => this.checkBundleVersions(),
                    10000 + Math.floor(Math.random() * 50) * 1000
                );
            }
        });
        this.busService.start();
    }

    /**
     * @returns {Promise<Object<string, string> | null>} `{ file: version }`, null on a failure
     */
    async fetchVersions() {
        try {
            return await rpc(VERSIONS_ROUTE, {}, { silent: true });
        } catch {
            return null;
        }
    }

    async checkBundleVersions() {
        const serverVersions = await this.fetchVersions();
        const isOutdated =
            hasVersions(serverVersions) && !shallowEqual(serverVersions, this.baselineVersions);
        if (isOutdated && !this.isNoticeDisplayed) {
            this.displayNotice();
        }
    }

    displayNotice() {
        this.isNoticeDisplayed = true;
        this.notification.add(_t("The page appears to be out of date."), {
            title: _t("Refresh"),
            type: "warning",
            sticky: true,
            buttons: [
                {
                    name: _t("Refresh"),
                    primary: true,
                    onClick: () => location.reload(),
                },
            ],
            onClose: () => {
                this.isNoticeDisplayed = false;
            },
        });
    }
}

services.add(BundleRefreshNoticePlugin);
