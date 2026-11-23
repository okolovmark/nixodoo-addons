/*
 * The renderer of an AI report's page: with Chart.js, the only script its frame runs, allowed by hash.
 * It fills the elements the page's bindings name from the data Odoo computed. Not part of any asset
 * bundle: ai.report inlines it into the frame.
 */
(function () {
    "use strict";

    const COLORS = [
        "#4e79a7",
        "#f28e2b",
        "#59a14f",
        "#e15759",
        "#76b7b2",
        "#edc948",
        "#b07aa1",
        "#ff9da7",
        "#9c755f",
        "#bab0ac",
    ];
    const NUMBER_FORMATS = ["number", "integer", "percent"];
    const report = JSON.parse(document.getElementById("ai-report-data").textContent);
    const locale = supportedLocale(report.lang);
    // The frame runs no Odoo code to translate with: the server sends these in the reader's language.
    const notes = report.notes;

    function supportedLocale(lang) {
        try {
            return Intl.NumberFormat.supportedLocalesOf([lang]).length ? lang : undefined;
        } catch {
            return undefined;
        }
    }

    function rowsOf(binding) {
        const dataset = report.datasets[binding.dataset];
        return dataset ? dataset.rows : [];
    }

    function isPartial(binding) {
        // The dataset's own limit left rows out: a figure over it is not the whole.
        const dataset = report.datasets[binding.dataset];
        return Boolean(dataset && dataset.has_more);
    }

    function noteText(key, counts) {
        return notes[key].replace(/%\((\w+)\)s/g, (placeholder, name) => String(counts[name]));
    }

    function appendNote(element, text) {
        const note = document.createElement("p");
        note.className = "report-table-note";
        note.textContent = text;
        element.after(note);
    }

    function isNumber(value) {
        return typeof value === "number" && Number.isFinite(value);
    }

    function asText(value) {
        if (value === null || value === undefined || value === false) {
            return "";
        }
        if (Array.isArray(value)) {
            return value.length === 2 && typeof value[1] === "string" ? value[1] : value.join(", ");
        }
        return String(value);
    }

    function formatNumber(value, digits) {
        return value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }

    function formatDate(value) {
        if (typeof value !== "string" || !/^\d{4}-\d\d-\d\d/.test(value)) {
            return asText(value);
        }
        const isDateOnly = value.length === 10;
        const date = new Date(isDateOnly ? `${value}T00:00:00` : value);
        if (Number.isNaN(date.getTime())) {
            return value;
        }
        return isDateOnly ? date.toLocaleDateString(locale) : date.toLocaleString(locale);
    }

    function formatValue(value, format, decimals) {
        if (format === "date") {
            return formatDate(value);
        }
        if (format === "text" || !isNumber(value)) {
            return asText(value);
        }
        const text = formatNumber(value, format === "integer" ? 0 : decimals);
        return format === "percent" ? `${text}%` : text;
    }

    function sum(values) {
        return values.reduce((total, value) => total + value, 0);
    }

    function pickValue(binding) {
        let rows = rowsOf(binding);
        if (binding.match_column) {
            rows = rows.filter((row) => asText(row[binding.match_column]) === binding.match_value);
        }
        if (binding.pick === "count") {
            return rows.length;
        }
        if (binding.pick === "first" || binding.pick === "last") {
            const row = binding.pick === "first" ? rows[0] : rows[rows.length - 1];
            return row ? row[binding.column] : null;
        }
        const numbers = rows.map((row) => row[binding.column]).filter(isNumber);
        if (!numbers.length) {
            return null;
        }
        const aggregates = {
            sum: () => sum(numbers),
            avg: () => sum(numbers) / numbers.length,
            min: () => Math.min(...numbers),
            max: () => Math.max(...numbers),
        };
        return aggregates[binding.pick]();
    }

    function renderValue(element, binding) {
        element.textContent = formatValue(pickValue(binding), binding.format, binding.decimals);
        if (isPartial(binding)) {
            const mark = document.createElement("span");
            mark.className = "report-partial";
            mark.title = notes.partial_title;
            mark.textContent = ` ${notes.partial_mark}`;
            element.appendChild(mark);
        }
    }

    function appendCell(row, tag, text, isNumeric) {
        const cell = document.createElement(tag);
        cell.textContent = text;
        if (isNumeric) {
            cell.className = "num";
        }
        row.appendChild(cell);
    }

    function renderTable(element, binding) {
        // A wide table scrolls in its wrapper on a phone, never the whole page.
        const wrapper = document.createElement("div");
        wrapper.className = "report-table-wrap";
        let table = element;
        if (element.tagName === "TABLE") {
            table.replaceChildren();
            element.replaceWith(wrapper);
            wrapper.appendChild(table);
        } else {
            table = document.createElement("table");
            wrapper.appendChild(table);
            element.replaceChildren(wrapper);
        }
        table.classList.add("report-table");
        const columns = binding.columns;
        const isNumeric = columns.map((column) => NUMBER_FORMATS.includes(column.format));
        const head = table.createTHead().insertRow();
        columns.forEach((column, index) => appendCell(head, "th", column.label, isNumeric[index]));
        const datasetRows = rowsOf(binding);
        const rows = binding.limit ? datasetRows.slice(0, binding.limit) : datasetRows;
        const body = table.createTBody();
        for (const row of rows) {
            const tableRow = body.insertRow();
            columns.forEach((column, index) =>
                appendCell(tableRow, "td", formatValue(row[column.column], column.format, column.decimals), isNumeric[index])
            );
        }
        if (binding.total_label) {
            const totalRow = table.createTFoot().insertRow();
            columns.forEach((column, index) => {
                if (column.format === "number" || column.format === "integer") {
                    const total = sum(rows.map((row) => row[column.column]).filter(isNumber));
                    appendCell(totalRow, "th", formatValue(total, column.format, column.decimals), true);
                } else {
                    appendCell(totalRow, "th", index === 0 ? binding.total_label : "", false);
                }
            });
        }
        const isCut = rows.length !== datasetRows.length;
        const counts = { shown: rows.length, total: datasetRows.length };
        if (isPartial(binding)) {
            appendNote(wrapper, noteText(isCut ? "table_cut_partial" : "table_partial", counts));
        } else if (isCut) {
            appendNote(wrapper, noteText("table_cut", counts));
        }
    }

    function renderChart(element, binding) {
        const rows = rowsOf(binding);
        const box = document.createElement("div");
        box.className = "report-chart";
        const canvas = document.createElement("canvas");
        box.appendChild(canvas);
        element.replaceChildren(box);
        const isRound = binding.type === "pie" || binding.type === "doughnut";
        const datasets = binding.series.map((series, index) => {
            const color = COLORS[index % COLORS.length];
            return {
                label: series.label,
                data: rows.map((row) => (isNumber(row[series.column]) ? row[series.column] : null)),
                backgroundColor: isRound ? rows.map((row, rowIndex) => COLORS[rowIndex % COLORS.length]) : color,
                borderColor: isRound ? "#ffffff" : color,
                fill: binding.type === "line" && binding.stacked,
            };
        });
        const options = {
            maintainAspectRatio: false,
            responsive: true,
            legend: { display: isRound || datasets.length > 1 },
            tooltips: {
                callbacks: {
                    label: (item, data) => {
                        const dataset = data.datasets[item.datasetIndex];
                        const name = isRound ? data.labels[item.index] : dataset.label;
                        const value = dataset.data[item.index];
                        return `${name}: ${isNumber(value) ? value.toLocaleString(locale) : ""}`;
                    },
                },
            },
        };
        if (!isRound) {
            const valueAxis = {
                stacked: binding.stacked,
                ticks: { beginAtZero: true, callback: (value) => value.toLocaleString(locale) },
            };
            const labelAxis = { stacked: binding.stacked };
            options.scales =
                binding.type === "horizontalBar"
                    ? { xAxes: [valueAxis], yAxes: [labelAxis] }
                    : { xAxes: [labelAxis], yAxes: [valueAxis] };
        }
        const labels = rows.map((row) => asText(row[binding.label_column]));
        new window.Chart(canvas, { type: binding.type, data: { labels, datasets }, options });
        if (isPartial(binding)) {
            appendNote(box, noteText("chart_partial", { shown: rows.length }));
        }
    }

    function render() {
        const renderers = { values: renderValue, tables: renderTable, charts: renderChart };
        for (const [kind, renderBinding] of Object.entries(renderers)) {
            for (const binding of report.bindings[kind] || []) {
                const element = document.getElementById(binding.id);
                if (!element) {
                    continue;
                }
                try {
                    renderBinding(element, binding);
                } catch (error) {
                    element.textContent = "";
                    console.warn(`AI report: the binding ${binding.id} could not be rendered`, error);
                }
            }
        }
        document.documentElement.setAttribute("data-report-rendered", "1");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", render);
    } else {
        render();
    }
})();
