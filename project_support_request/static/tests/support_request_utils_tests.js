/** @odoo-module **/

import { startServer } from "@bus/../tests/helpers/mock_python_environment";
import { priorityDot, ticketAge } from "@project_support_request/support_request_systray/support_request_utils";

const { DateTime } = luxon;

QUnit.module(
    "project_support_request",
    {
        // The bus test helpers fetch model definitions when the run begins; a run of these tests
        // alone ends before that request does, and leaving the page aborts it: a global failure.
        before: () => startServer(),
    },
    () => {
        QUnit.module("priorityDot");

        QUnit.test("the dot strengthens from Lowest to Highest, each with its label", (assert) => {
            assert.deepEqual(
                ["0", "1", "2", "3", "4"].map(priorityDot),
                [
                    { className: "bg-600 opacity-50", label: "Lowest" },
                    { className: "bg-600", label: "Low" },
                    { className: "bg-info", label: "Medium" },
                    { className: "bg-warning", label: "High" },
                    { className: "bg-danger", label: "Highest" },
                ]
            );
        });

        QUnit.test("a request without a known level gets the Low dot", (assert) => {
            assert.deepEqual([false, "9"].map(priorityDot), [priorityDot("1"), priorityDot("1")]);
        });

        QUnit.module("ticketAge");

        QUnit.test("the create date is read in UTC, whatever the browser's time zone", (assert) => {
            const now = DateTime.fromISO("2026-11-19T10:30:00Z");
            assert.strictEqual(ticketAge("2026-11-19 10:00:00", now), "30m ago");
            assert.strictEqual(ticketAge("2026-11-19 07:15:00", now), "3h ago");
            assert.strictEqual(ticketAge("2026-11-16 10:30:00", now), "3d ago");
        });

        QUnit.test("a create date after the client's clock is 0m ago", (assert) => {
            const now = DateTime.fromISO("2026-11-19T10:30:00Z");
            assert.strictEqual(ticketAge("2026-11-19 10:32:00", now), "0m ago");
        });

        QUnit.test("a request without a create date has no age", (assert) => {
            assert.strictEqual(ticketAge(""), "");
        });
    }
);
