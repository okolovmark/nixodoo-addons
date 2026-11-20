/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

const VERSIONS_ROUTE = "/web_bundle_refresh_notice/versions";
const BUNDLE_ELEMENT_SELECTOR = [
    "script[data-asset-bundle][data-asset-version]",
    "link[data-asset-bundle][data-asset-version]",
].join(", ");

/**
 * The bundles a page loaded, as `{ name, version }`, one per `script` or `link` element the server
 * marked with `data-asset-bundle` and `data-asset-version`.
 *
 * @param {Document|Element} root
 * @returns {{ name: string, version: string }[]}
 */
export function readLoadedBundles(root) {
    return [...root.querySelectorAll(BUNDLE_ELEMENT_SELECTOR)].map((element) => ({
        name: element.dataset.assetBundle,
        version: element.dataset.assetVersion,
    }));
}

/**
 * Odoo's own refresh notice (`assetsWatchdog`) for a bundle change that keeps the server version,
 * a module deploy: the watchdog only speaks when the server version differs.
 */
export const bundleRefreshNoticeService = {
    dependencies: ["bus_service", "notification", "rpc"],

    // A method, so a test hands the service a page of its own.
    getLoadedBundles() {
        return readLoadedBundles(document);
    },

    start(env, { bus_service, notification, rpc }) {
        const loadedBundles = this.getLoadedBundles();
        if (!loadedBundles.length) {
            return;
        }
        let isNoticeDisplayed = false;
        let checkTimeoutId = null;

        function displayNotice() {
            isNoticeDisplayed = true;
            notification.add(env._t("The page appears to be out of date."), {
                title: env._t("Refresh"),
                type: "warning",
                sticky: true,
                buttons: [
                    {
                        name: env._t("Refresh"),
                        primary: true,
                        onClick: () => browser.location.reload(),
                    },
                ],
                onClose: () => {
                    isNoticeDisplayed = false;
                },
            });
        }

        async function checkBundleVersions() {
            let serverVersions;
            try {
                serverVersions = await rpc(VERSIONS_ROUTE, {}, { silent: true });
            } catch {
                return;
            }
            const isOutdated = loadedBundles.some(
                ({ name, version }) => Object.hasOwn(serverVersions, name) && serverVersions[name] !== version
            );
            if (isOutdated && !isNoticeDisplayed) {
                displayNotice();
            }
        }

        bus_service.addEventListener("notification", ({ detail: notifications }) => {
            const hasBundleChanged = notifications.some(
                ({ type, payload }) =>
                    type === "bundle_changed" && payload.server_version === session.server_version
            );
            if (hasBundleChanged && !isNoticeDisplayed) {
                // The watchdog's random wait, so open tabs do not all ask, nor reload, at once.
                browser.clearTimeout(checkTimeoutId);
                checkTimeoutId = browser.setTimeout(
                    checkBundleVersions,
                    10000 + Math.floor(Math.random() * 50) * 1000
                );
            }
        });
        bus_service.start();
    },
};

registry.category("services").add("bundleRefreshNotice", bundleRefreshNoticeService);
