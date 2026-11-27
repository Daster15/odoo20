import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Always-visible top bar (systray) shortcut to the Document Search client action.
 * Hotkey: Alt+Shift+F.
 */
export class DocSearchSystray extends Component {
    static template = "ltech_doc_search.DocSearchSystray";

    setup() {
        this.actionService = useService("action");
    }

    open() {
        this.actionService.doAction("ltech_doc_search.action_doc_search", { clearBreadcrumbs: true });
    }
}

registry.category("systray").add("ltech_doc_search.systray", { Component: DocSearchSystray }, { sequence: 90 });
