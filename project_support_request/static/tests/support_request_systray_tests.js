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
import { getFixture, mount, patchWithCleanup } from "@web/../tests/helpers/utils";
import { SupportRequestSystray } from "@project_support_request/support_request_systray/support_request_systray";

const CONFIG_URL = "/project_support_request/config";
const CREATE_URL = "/project_support_request/create";
const SYSTRAY_URL = "/project_support_request/systray";

// The same hook as in support_request_utils_tests.js, for a run of only these tests.
QUnit.module("project_support_request", { before: () => startServer() }, () => {
    QUnit.module("SupportRequestSystray");

    QUnit.test("its notifications and the closed tickets action read in the user's language", async (assert) => {
        patchWithCleanup(translatedTerms, {
            "Closed Tickets": "Tickets clos",
            "Please enter a ticket title.": "Saisissez un titre.",
            "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo on the AI-driven project that develops it, or restores the archived one that carries it.":
                "Aucun projet actif ne développe cet Odoo.",
            "Error opening closed tickets": "Erreur à l'ouverture des tickets clos",
            "Error creating ticket": "Erreur à la création du ticket",
            "Error loading tickets": "Erreur au chargement des tickets",
        });
        let supportProjectId = 7;
        let failingUrl = null;
        const serviceRegistry = registry.category("services");
        serviceRegistry.add("localization", makeFakeLocalizationService());
        serviceRegistry.add("notification", makeFakeNotificationService((message) => assert.step(message)));
        serviceRegistry.add("action", { start: () => ({ doAction: (action) => assert.step(action.name) }) });
        // Forced: startServer() has registered the mock server's rpc for this test already.
        serviceRegistry.add(
            "rpc",
            makeFakeRPCService(async (url) => {
                if (url === failingUrl) {
                    throw new Error("The server is down.");
                }
                if (url === CONFIG_URL) {
                    return { project_id: supportProjectId, uid: 2, task_types: [] };
                }
                return { configured: true, bugs_count: 0, others_count: 0, tickets: [], total: 0 };
            }),
            { force: true }
        );
        const systray = await mount(SupportRequestSystray, getFixture(), { env: await makeTestEnv() });

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
            "Tickets clos",
            "Saisissez un titre.",
            "Aucun projet actif ne développe cet Odoo.",
            "Erreur à l'ouverture des tickets clos",
            "Erreur à la création du ticket",
            "Erreur au chargement des tickets",
        ]);
    });
});
