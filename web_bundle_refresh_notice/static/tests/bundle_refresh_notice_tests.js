/** @odoo-module **/

import { startServer } from "@bus/../tests/helpers/mock_python_environment";
import { multiTabService } from "@bus/multi_tab_service";
import { assetsWatchdogService } from "@bus/services/assets_watchdog_service";
import { busService } from "@bus/services/bus_service";
import { browser } from "@web/core/browser/browser";
import { RPCError } from "@web/core/network/rpc_service";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { clearRegistryWithCleanup } from "@web/../tests/helpers/mock_env";
import {
    click,
    getFixture,
    mockTimeout,
    nextTick,
    patchWithCleanup,
} from "@web/../tests/helpers/utils";
import { createWebClient } from "@web/../tests/webclient/helpers";
import {
    bundleRefreshNoticeService,
    readLoadedBundles,
} from "@web_bundle_refresh_notice/bundle_refresh_notice_service";

const VERSIONS_ROUTE = "/web_bundle_refresh_notice/versions";
const SHORTEST_WAIT = 10000;
const LONGEST_WAIT = 59000;

const UNMARKED_ELEMENTS = `
    <script type="text/javascript" src="/web/static/lib/luxon/luxon.js"></script>
    <link rel="shortcut icon" href="/web/static/img/favicon.ico"/>`;
const PAGE = `
    <link type="text/css" rel="stylesheet" href="/web/assets/11-aaaaaaa/web.assets_common.min.css"
        data-asset-bundle="web.assets_common" data-asset-version="aaaaaaa"/>
    <link type="text/css" rel="stylesheet" href="/web/assets/12-bbbbbbb/rtl/web.assets_backend.min.css"
        data-asset-bundle="web.assets_backend" data-asset-version="bbbbbbb"/>
    <script type="text/javascript" src="/web/assets/13-aaaaaaa/web.assets_common.min.js"
        data-asset-bundle="web.assets_common" data-asset-version="aaaaaaa"></script>
    <script type="text/javascript" src="/web/assets/14-bbbbbbb/1/web.assets_backend.min.js"
        data-asset-bundle="web.assets_backend" data-asset-version="bbbbbbb"></script>
    <script type="text/javascript" src="/web/assets/15-ddddddd/web.assets_backend_prod_only.min.js"
        data-asset-bundle="web.assets_backend_prod_only" data-asset-version="ddddddd"></script>
    ${UNMARKED_ELEMENTS}`;
const DEBUG_ASSETS_PAGE = `
    <script type="text/javascript" src="/web/assets/debug/web.assets_backend.js"
        data-asset-bundle="web.assets_backend" data-asset-version="bbbbbbb"></script>`;
const CURRENT_VERSIONS = { "web.assets_common": "aaaaaaa", "web.assets_backend": "bbbbbbb" };
const DEPLOYED_VERSIONS = { "web.assets_common": "aaaaaaa", "web.assets_backend": "ccccccc" };

// A parsed document loads none of its scripts or stylesheets.
function parsePage(html) {
    return new DOMParser().parseFromString(`<html><head>${html}</head></html>`, "text/html");
}

QUnit.module("web_bundle_refresh_notice", (hooks) => {
    let target;
    let advanceTime;
    let pyEnv;

    hooks.beforeEach((assert) => {
        // Main components other installed modules register may need services this test does not start.
        clearRegistryWithCleanup(registry.category("main_components"));
        const serviceRegistry = registry.category("services");
        serviceRegistry.add("bus_service", busService);
        serviceRegistry.add("multi_tab", multiTabService);
        serviceRegistry.add("assetsWatchdog", assetsWatchdogService);
        serviceRegistry.add("bundleRefreshNotice", bundleRefreshNoticeService);
        patchWithCleanup(session, { server_version: "16.0" });
        patchWithCleanup(browser, { location: { reload: () => assert.step("reload") } });
        ({ advanceTime } = mockTimeout());
        target = getFixture();
    });

    /**
     * @param {Object} assert
     * @param {Object} params
     * @param {Function} params.answerVersions the versions route's answer; a throw is its error
     * @param {string} [params.page] the asset tags of the page the tab loaded
     */
    async function startTab(assert, { answerVersions, page = PAGE }) {
        patchWithCleanup(bundleRefreshNoticeService, {
            getLoadedBundles: () => readLoadedBundles(parsePage(page)),
        });
        pyEnv = await startServer();
        await createWebClient({
            mockRPC(route) {
                if (route === VERSIONS_ROUTE) {
                    assert.step(route);
                    return answerVersions();
                }
            },
        });
    }

    async function sendBundleChanged(serverVersion = session.server_version) {
        pyEnv["bus.bus"]._sendone("broadcast", "bundle_changed", { server_version: serverVersion });
        await nextTick();
    }

    QUnit.test("each bundle element gives its bundle and version whatever its URL, others nothing", (assert) => {
        assert.deepEqual(readLoadedBundles(parsePage(PAGE)), [
            { name: "web.assets_common", version: "aaaaaaa" },
            { name: "web.assets_backend", version: "bbbbbbb" },
            { name: "web.assets_common", version: "aaaaaaa" },
            { name: "web.assets_backend", version: "bbbbbbb" },
            { name: "web.assets_backend_prod_only", version: "ddddddd" },
        ]);
        assert.deepEqual(readLoadedBundles(parsePage(DEBUG_ASSETS_PAGE)), [
            { name: "web.assets_backend", version: "bbbbbbb" },
        ]);
        assert.deepEqual(readLoadedBundles(parsePage(UNMARKED_ELEMENTS)), []);
    });

    QUnit.test("a bundle the server now has in another version brings Odoo's refresh notice", async (assert) => {
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS });

        await sendBundleChanged();
        await advanceTime(SHORTEST_WAIT - 1);
        assert.verifySteps([], "the tab waits as long as Odoo's own notice does");
        await advanceTime(LONGEST_WAIT);
        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsOnce(target, ".o_notification.border-warning");
        assert.strictEqual(target.querySelector(".o_notification_title").textContent, "Refresh");
        assert.strictEqual(
            target.querySelector(".o_notification_content").textContent,
            "The page appears to be out of date."
        );
        assert.strictEqual(target.querySelector(".o_notification_buttons").textContent.trim(), "Refresh");

        await advanceTime(LONGEST_WAIT);
        assert.containsOnce(target, ".o_notification", "the notice is sticky");
        await click(target, ".o_notification_buttons .btn-primary");
        assert.verifySteps(["reload"]);
    });

    QUnit.test("no notice while the server has the versions the tab loaded", async (assert) => {
        await startTab(assert, { answerVersions: () => CURRENT_VERSIONS });

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsNone(target, ".o_notification", "the page's untracked bundle is not compared");
    });

    QUnit.test("a debug=assets tab gets the notice too", async (assert) => {
        patchWithCleanup(odoo, { debug: "assets" });
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS, page: DEBUG_ASSETS_PAGE });

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsOnce(target, ".o_notification.border-warning");
    });

    QUnit.test("the server is asked silently, without the loading indicator", async (assert) => {
        patchWithCleanup(bundleRefreshNoticeService, {
            start(env, services) {
                const rpc = (route, params, settings) => {
                    assert.step(`${route} ${JSON.stringify(settings)}`);
                    return services.rpc(route, params, settings);
                };
                return this._super(env, { ...services, rpc });
            },
        });
        await startTab(assert, { answerVersions: () => CURRENT_VERSIONS });

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([`${VERSIONS_ROUTE} {"silent":true}`, VERSIONS_ROUTE]);
    });

    QUnit.test("a burst of bundle changes asks the server once and shows one notice", async (assert) => {
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS });

        await sendBundleChanged();
        await advanceTime(SHORTEST_WAIT - 1000);
        await sendBundleChanged();
        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsOnce(target, ".o_notification");
    });

    QUnit.test("one notice at a time, and again after it is closed", async (assert) => {
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS });
        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);
        assert.verifySteps([VERSIONS_ROUTE]);

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);
        assert.verifySteps([], "the notice on screen already says it");
        assert.containsOnce(target, ".o_notification");

        await click(target, ".o_notification_close");
        assert.containsNone(target, ".o_notification");
        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);
        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsOnce(target, ".o_notification");
    });

    QUnit.test("a change of server version is left to Odoo's own notice", async (assert) => {
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS });

        await sendBundleChanged("17.0");
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([], "the server is not asked");
        assert.containsOnce(target, ".o_notification", "Odoo's notice, not a second one");
    });

    QUnit.test("a tab without bundle elements stays quiet", async (assert) => {
        await startTab(assert, { answerVersions: () => DEPLOYED_VERSIONS, page: UNMARKED_ELEMENTS });

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([]);
        assert.containsNone(target, ".o_notification");
    });

    QUnit.test("a failed request shows nothing", async (assert) => {
        await startTab(assert, {
            answerVersions: () => {
                throw new RPCError("Odoo Session Expired");
            },
        });

        await sendBundleChanged();
        await advanceTime(LONGEST_WAIT);

        assert.verifySteps([VERSIONS_ROUTE]);
        assert.containsNone(target, ".o_notification");
    });
});
