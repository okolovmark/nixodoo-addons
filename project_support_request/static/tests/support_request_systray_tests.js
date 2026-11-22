/** @odoo-module **/

import { startServer } from "@bus/../tests/helpers/mock_python_environment";
import { translatedTerms } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { makeTestEnv } from "@web/../tests/helpers/mock_env";
import {
    makeFakeLocalizationService,
    makeFakeNotificationService,
    makeFakeRPCService,
} from "@web/../tests/helpers/mock_services";
import { editSelect, getFixture, mount, nextTick, patchWithCleanup } from "@web/../tests/helpers/utils";
import { SupportRequestSystray } from "@project_support_request/support_request_systray/support_request_systray";

const CONFIG_URL = "/project_support_request/config";
const CREATE_URL = "/project_support_request/create";
const SYSTRAY_URL = "/project_support_request/systray";

async function mountSystray(assert, rpcHandler) {
    const serviceRegistry = registry.category("services");
    serviceRegistry.add("localization", makeFakeLocalizationService());
    serviceRegistry.add("notification", makeFakeNotificationService((message) => assert.step(message)));
    serviceRegistry.add("action", {
        start: () => ({
            doAction: (action) => assert.step(action.name || `open ${action.res_model} ${action.res_id}`),
        }),
    });
    // Forced: startServer() has registered the mock server's rpc for this test already.
    serviceRegistry.add("rpc", makeFakeRPCService(rpcHandler), { force: true });
    return mount(SupportRequestSystray, getFixture(), { env: await makeTestEnv() });
}

function routeTicket(id, values = {}) {
    return {
        id,
        key: "",
        name: `Request ${id}`,
        task_type: "bug",
        stage_name: "Backlog",
        priority_level: "1",
        create_date: "2026-11-19 10:00:00.123456",
        sequence: 10,
        ...values,
    };
}

function textsOf(selector) {
    return [...getFixture().querySelectorAll(selector)].map((element) => element.textContent.trim());
}

// The same hook as in support_request_utils_tests.js, for a run of only these tests.
QUnit.module("project_support_request", { before: () => startServer() }, () => {
    QUnit.module("SupportRequestSystray");

    QUnit.test("its notifications and the closed requests action read in the user's language", async (assert) => {
        patchWithCleanup(translatedTerms, {
            "Closed Support Requests": "Demandes d'assistance closes",
            "Please enter a title for the support request.": "Saisissez un titre.",
            "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo on the AI-driven project that develops it, or restores the archived one that carries it.":
                "Aucun projet actif ne développe cet Odoo.",
            "Error opening closed support requests": "Erreur à l'ouverture des demandes closes",
            "Error creating the support request": "Erreur à la création de la demande",
            "Error loading support requests": "Erreur au chargement des demandes",
        });
        let supportProjectId = 7;
        let failingUrl = null;
        const systray = await mountSystray(assert, async (url) => {
            if (url === failingUrl) {
                throw new Error("The server is down.");
            }
            if (url === CONFIG_URL) {
                return { project_id: supportProjectId, uid: 2, task_types: [] };
            }
            return { configured: true, bugs_count: 0, others_count: 0, tickets: [], total: 0 };
        });

        await systray.onViewClosedClick();
        await systray.onSubmitCreate();
        supportProjectId = false;
        await systray.onViewClosedClick();
        failingUrl = CONFIG_URL;
        await systray.onViewClosedClick();
        failingUrl = CREATE_URL;
        systray.state.createName = "A request";
        await systray.onSubmitCreate();
        failingUrl = SYSTRAY_URL;
        await systray.onShowMore();

        assert.verifySteps([
            "Demandes d'assistance closes",
            "Saisissez un titre.",
            "Aucun projet actif ne développe cet Odoo.",
            "Erreur à l'ouverture des demandes closes",
            "Erreur à la création de la demande",
            "Erreur au chargement des demandes",
        ]);
    });

    QUnit.test("the priority dot of a request is titled in the user's language", async (assert) => {
        patchWithCleanup(translatedTerms, { "Priority: %s": "Priorité : %s", Highest: "Très haute" });
        const ticket = routeTicket(1, { priority_level: "4" });
        const systray = await mountSystray(assert, async (url) =>
            url === CONFIG_URL
                ? { project_id: 7, uid: 2, task_types: [] }
                : { configured: true, bugs_count: 1, others_count: 0, tickets: [ticket], total: 1 }
        );

        systray.state.isOpen = true;
        await nextTick();

        const dot = getFixture().querySelector(".o_requests_priority_dot");
        assert.deepEqual(
            [dot.getAttribute("title"), dot.getAttribute("aria-label")],
            ["Priorité : Très haute", "Priorité : Très haute"]
        );
    });

    QUnit.test("the panel shows the counts, the requests and the task types the routes send", async (assert) => {
        const tickets = [1, 2, 3, 4, 5, 6, 7].map((id) => routeTicket(id));
        const systray = await mountSystray(assert, async (url, params) => {
            if (url === CONFIG_URL) {
                return {
                    project_id: 7,
                    uid: 2,
                    task_types: [
                        { value: "bug", label: "Bug" },
                        { value: "feature", label: "Feature" },
                    ],
                };
            }
            return {
                configured: true,
                bugs_count: tickets.length,
                others_count: 2,
                tickets: params.limit === 0 ? tickets : tickets.slice(0, 5),
                total: tickets.length,
            };
        });

        systray.state.isOpen = true;
        await nextTick();
        assert.deepEqual(textsOf(".o_odoo_requests_badge, .o_requests_tab .badge"), ["9", "7", "2"]);
        assert.deepEqual(textsOf(".o_odoo_requests_ticket_item strong"), [
            "Request 1",
            "Request 2",
            "Request 3",
            "Request 4",
            "Request 5",
        ]);
        assert.deepEqual(textsOf(".o_odoo_requests_ticket_list a"), ["View more (2 more)"]);

        tickets.push(routeTicket(8, { name: "Filed meanwhile" }));
        await systray.onShowMore();
        await nextTick();
        assert.deepEqual(
            [textsOf(".o_odoo_requests_ticket_item strong").at(-1), textsOf(".o_odoo_requests_ticket_list a")],
            ["Filed meanwhile", []],
            "View more lists every request and goes away"
        );

        systray.onShowCreateForm();
        await nextTick();
        assert.deepEqual(textsOf(".o_odoo_requests_dropdown option"), ["Bug", "Feature"]);
    });

    QUnit.test("a request is filed with the type its form shows", async (assert) => {
        const systray = await mountSystray(assert, async (url, params) => {
            if (url === CONFIG_URL) {
                return {
                    project_id: 7,
                    uid: 2,
                    task_types: [
                        { value: "bug", label: "Bug" },
                        { value: "improvement", label: "Improvement" },
                        { value: "feature", label: "Feature" },
                    ],
                };
            }
            if (url === CREATE_URL) {
                assert.step(`${params.task_type} filed`);
                return { ticket_id: 42 };
            }
            return { configured: true, bugs_count: 0, others_count: 0, tickets: [], total: 0 };
        });

        async function fileRequest(pickedType) {
            systray.state.isOpen = true;
            systray.onShowCreateForm();
            await nextTick();
            if (pickedType) {
                await editSelect(getFixture(), ".o_odoo_requests_dropdown select", pickedType);
            }
            assert.step(textsOf(".o_odoo_requests_dropdown option:checked").join());
            systray.state.createName = "A request";
            await systray.onSubmitCreate();
        }

        await fileRequest();
        await systray.onSwitchTab("others");
        await fileRequest();
        await fileRequest("feature");

        assert.verifySteps([
            "Bug",
            "bug filed",
            "open project.task 42",
            "Improvement",
            "improvement filed",
            "open project.task 42",
            "Feature",
            "feature filed",
            "open project.task 42",
        ]);
    });
});
