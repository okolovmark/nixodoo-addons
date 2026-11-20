import { defineBusModels } from "@bus/../tests/bus_test_helpers";
import { BusPlugin } from "@bus/services/bus_plugin";
import { advanceTime, after, animationFrame, describe, expect, test } from "@odoo/hoot";
import {
    contains,
    getService,
    makeServerError,
    MockServer,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { location } from "@web/core/browser/browser";
import { rpcBus } from "@web/core/network/rpc";
import { session } from "@web/session";
import { WebClient } from "@web/webclient/webclient";

defineBusModels();
describe.current.tags("desktop");

const VERSIONS_ROUTE = "/web_bundle_refresh_notice/versions";
const SHORTEST_WAIT = 10000;
const LONGEST_WAIT = 59000;

const SCRIPT = "web.assets_web.min.js";
const STYLESHEET = "web.assets_web.min.css";
const BASELINE_VERSIONS = { [SCRIPT]: "bbbbbbb", [STYLESHEET]: "ccccccc" };
const DEPLOYED_VERSIONS = { [SCRIPT]: "ddddddd", [STYLESHEET]: "ccccccc" };
const RESTYLED_VERSIONS = { [SCRIPT]: "bbbbbbb", [STYLESHEET]: "eeeeeee" };

function makeSessionExpiredError() {
    return makeServerError({
        errorName: "odoo.http.session.SessionExpiredException",
        message: "Odoo Session Expired",
    });
}

/**
 * Starts a tab whose page carries the given baseline, and whose requests to the versions route get
 * the given answers in turn, the last one for every later request; an error answer is the
 * request's error.
 *
 * @param {Object<string, string> | undefined} baselineVersions none in the page when undefined
 * @param {...(Object<string, string> | Error)} answers
 */
async function startTab(baselineVersions, ...answers) {
    if (baselineVersions !== undefined) {
        patchWithCleanup(session, { bundle_versions: baselineVersions });
    }
    onRpc(VERSIONS_ROUTE, () => {
        expect.step(VERSIONS_ROUTE);
        const answer = answers.length > 1 ? answers.shift() : answers[0];
        if (answer instanceof Error) {
            throw answer;
        }
        return answer;
    });
    location.addEventListener("reload", () => expect.step("reload"));
    await mountWithCleanup(WebClient);
    expect.verifySteps([], { message: "the tab takes its baseline from its page, not the server" });
}

async function sendBundleChanged(serverVersion = session.server_version) {
    // The tab's own listener came first: once this one hears the message, the tab has too.
    const isDelivered = new Promise((resolve) => {
        const unsubscribe = getService(BusPlugin).subscribe("bundle_changed", () => {
            unsubscribe();
            resolve();
        });
    });
    MockServer.env["bus.bus"]._sendone("broadcast", "bundle_changed", {
        server_version: serverVersion,
    });
    await isDelivered;
}

async function waitLongest() {
    await advanceTime(LONGEST_WAIT);
    await animationFrame();
}

test("a baseline file in another version brings Odoo's refresh notice", async () => {
    await startTab(BASELINE_VERSIONS, DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await advanceTime(SHORTEST_WAIT - 1);
    expect.verifySteps([], { message: "the tab waits as long as Odoo's own notice does" });
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
    expect(".o_notification_bar").toHaveClass("bg-warning");
    expect(".o_notification_content").toHaveText("Refresh. The page appears to be out of date.");
    expect(".o_notification_buttons").toHaveText("Refresh");

    await waitLongest();
    expect(".o_notification").toHaveCount(1, { message: "the notice is sticky" });
    await contains(".o_notification_buttons .btn-primary").click();
    expect.verifySteps(["reload"]);
});

test("a stylesheet in another version brings the notice too", async () => {
    await startTab(BASELINE_VERSIONS, RESTYLED_VERSIONS);

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("no notice while the server gives the baseline versions", async () => {
    await startTab(BASELINE_VERSIONS, { ...BASELINE_VERSIONS });

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(0);
});

test("a baseline file the server no longer gives brings the notice", async () => {
    await startTab(BASELINE_VERSIONS, { [SCRIPT]: BASELINE_VERSIONS[SCRIPT] });

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("a file the baseline lacks brings the notice", async () => {
    await startTab(BASELINE_VERSIONS, {
        ...BASELINE_VERSIONS,
        "web.assets_web.rtl.min.css": "fffffff",
    });

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("the server is asked silently, without the loading indicator", async () => {
    const requestSettings = [];
    const recordRequestSettings = ({ detail: { url, settings } }) => {
        if (url === VERSIONS_ROUTE) {
            requestSettings.push(settings);
        }
    };
    rpcBus.addEventListener("RPC:REQUEST", recordRequestSettings);
    after(() => rpcBus.removeEventListener("RPC:REQUEST", recordRequestSettings));
    await startTab(BASELINE_VERSIONS, { ...BASELINE_VERSIONS });

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(requestSettings).toEqual([{ silent: true }]);
});

test("a burst of bundle changes asks the server once and shows one notice", async () => {
    await startTab(BASELINE_VERSIONS, DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await advanceTime(SHORTEST_WAIT - 1000);
    await sendBundleChanged();
    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("one notice at a time, and again after it is closed", async () => {
    await startTab(BASELINE_VERSIONS, DEPLOYED_VERSIONS);
    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);

    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([], { message: "the notice on screen already says it" });
    expect(".o_notification").toHaveCount(1);

    await contains(".o_notification_close").click();
    expect(".o_notification").toHaveCount(0);
    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("a change of server version is left to Odoo's own notice", async () => {
    await startTab(BASELINE_VERSIONS, DEPLOYED_VERSIONS);

    await sendBundleChanged("NEW_MAJOR_VERSION");
    await waitLongest();

    expect.verifySteps([], { message: "the server is not asked" });
    expect(".o_notification").toHaveCount(1, { message: "Odoo's notice, not a second one" });
});

test("a page that carries no baseline leaves its tab quiet", async () => {
    await startTab(undefined, DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([], { message: "the server is not asked" });
    expect(".o_notification").toHaveCount(0);
});

test("an empty baseline leaves the tab quiet", async () => {
    await startTab({}, DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await waitLongest();

    expect.verifySteps([], { message: "the server is not asked" });
    expect(".o_notification").toHaveCount(0);
});

test("a failed request shows nothing, and the tab keeps its baseline", async () => {
    await startTab(BASELINE_VERSIONS, makeSessionExpiredError(), DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(0);

    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});

test("an empty answer shows nothing, and the tab keeps its baseline", async () => {
    await startTab(BASELINE_VERSIONS, {}, DEPLOYED_VERSIONS);

    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(0);

    await sendBundleChanged();
    await waitLongest();
    expect.verifySteps([VERSIONS_ROUTE]);
    expect(".o_notification").toHaveCount(1);
});
