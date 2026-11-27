import { Component, markup, onMounted, onWillUnmount, proxy, signal, t, useProps } from "@odoo/owl";
import { useLayoutEffect } from "@web/owl2/utils";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { debounce } from "@web/core/utils/timing";
import { RecordSelector } from "@web/core/record_selectors/record_selector";

const POLL_MS = 3000;
const POLL_MAX = 60; // ~3 min

const TYPES = {
    pdf: { label: "PDF", icon: "fa-file-pdf-o text-danger" },
    word: { label: "Word", icon: "fa-file-word-o text-primary" },
    excel: { label: "Excel", icon: "fa-file-excel-o text-success" },
    powerpoint: { label: "PowerPoint", icon: "fa-file-powerpoint-o text-warning" },
    image: { label: _t("Images"), icon: "fa-file-image-o text-info" },
    email: { label: _t("Email"), icon: "fa-envelope-o" },
    text: { label: _t("Text"), icon: "fa-file-text-o" },
    other: { label: _t("Other"), icon: "fa-file-o text-muted" },
};

export class DocSearch extends Component {
    static template = "ltech_doc_search.DocSearch";
    static components = { RecordSelector };
    // Owl 3: props schema via useProps (static props is rejected)
    props = useProps({ ...standardActionServiceProps });
    // Owl 3: refs are signals, bound in the template with t-ref="this.xxx"
    fileInput = signal(null, { type: t.ref() });
    searchInput = signal(null, { type: t.ref() });
    previewText = signal(null, { type: t.ref() });
    previewView = signal(null, { type: t.ref() });

    setup() {
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.upload = proxy({
            open: false,
            projectId: false,
            taskId: false,
            dragOver: false,
            busy: false,
            items: [],
        });
        this._poll = null;
        onWillUnmount(() => this._poll && clearInterval(this._poll));
        this.TYPES = TYPES;
        this._token = 0;
        this.state = proxy({
            q: "",
            loading: false,
            searched: false,
            results: [],
            total: 0,
            page: 0,
            limit: 20,
            sort: "score",
            facets: { projects: [], types: [] },
            projectIds: [],
            fileTypes: [],
            selected: null,
            tab: "text",
            previewHtml: null,
            previewLoading: false,
            layout: null, // OCR highlight boxes of the selected image / scanned PDF
            matchIndex: 0,
            matchCount: 0,
        });
        this.debouncedSearch = debounce(() => this.search(0), 350);

        onMounted(() => this.searchInput()?.focus());

        // after the preview renders: count matches (<mark> in the text, OCR boxes
        // on images / scanned pages) and jump to the first one
        useLayoutEffect(
            () => {
                const marks = this.matchElements();
                this.state.matchCount = marks.length;
                this.state.matchIndex = 0;
                if (marks.length) {
                    this.focusMatch(0);
                }
            },
            () => [this.state.previewHtml, this.state.tab, this.state.layout]
        );
    }

    // ---------------------------------------------------------------- search

    onInput(ev) {
        this.state.q = ev.target.value;
        this.debouncedSearch();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.search(0);
        } else if (ev.key === "Escape") {
            this.state.selected = null;
        }
    }

    onSortChange(ev) {
        this.state.sort = ev.target.value;
        this.search(0);
    }

    async search(page = 0) {
        const q = this.state.q.trim();
        if (q.length < 2) {
            Object.assign(this.state, { searched: false, results: [], total: 0, selected: null });
            return;
        }
        const token = ++this._token;
        this.state.loading = true;
        try {
            const res = await rpc("/doc_search/query", {
                q,
                project_ids: this.state.projectIds,
                file_types: this.state.fileTypes,
                sort: this.state.sort,
                page,
                limit: this.state.limit,
            });
            if (token !== this._token) {
                return; // a newer search is in flight
            }
            Object.assign(this.state, {
                searched: true,
                page,
                total: res.total,
                facets: res.facets,
                results: res.results.map((r) => ({
                    ...r,
                    name_html: markup(r.name_html),
                    snippets: r.snippets.map((s) => markup(s)),
                })),
            });
            const sel = this.state.selected && this.state.results.find((r) => r.id === this.state.selected.id);
            if (!sel) {
                this.state.selected = null;
            } else {
                // query changed -> refresh highlights of the open preview
                this.state.selected = sel;
                this.state.previewHtml = null;
                this.state.layout = null;
                this.setTab(this.state.tab);
            }
        } finally {
            if (token === this._token) {
                this.state.loading = false;
            }
        }
    }

    toggle(listName, value) {
        const list = this.state[listName];
        const i = list.indexOf(value);
        if (i >= 0) {
            list.splice(i, 1);
        } else {
            list.push(value);
        }
        this.search(0);
    }

    clearFilters() {
        this.state.projectIds = [];
        this.state.fileTypes = [];
        this.search(0);
    }

    get pageCount() {
        return Math.ceil(this.state.total / this.state.limit);
    }

    // --------------------------------------------------------------- preview

    hasVisualPreview(r) {
        return r && (r.file_type === "pdf" || r.file_type === "image");
    }

    select(r) {
        this.state.selected = r;
        this.state.previewHtml = null;
        this.state.layout = null;
        this.setTab(this.hasVisualPreview(r) ? "view" : "text");
    }

    async setTab(tab) {
        this.state.tab = tab;
        if (tab === "view" && this.state.selected.ocr_pages && !this.state.layout) {
            const id = this.state.selected.id;
            const res = await rpc("/doc_search/boxes", { attachment_id: id, q: this.state.q.trim() });
            if (this.state.selected && this.state.selected.id === id) {
                this.state.layout = res;
            }
        }
        if (tab === "text" && this.state.previewHtml === null) {
            const id = this.state.selected.id;
            this.state.previewLoading = true;
            try {
                const res = await rpc("/doc_search/preview", { attachment_id: id, q: this.state.q.trim() });
                if (this.state.selected && this.state.selected.id === id) {
                    this.state.previewHtml = markup(res.html || "");
                }
            } finally {
                this.state.previewLoading = false;
            }
        }
    }

    pdfUrl(r) {
        const file = encodeURIComponent(`/web/content/${r.id}`);
        const search = encodeURIComponent(this.state.q.replace(/"/g, "").trim());
        return `/web/static/lib/pdfjs/web/viewer.html?file=${file}#search=${search}`;
    }

    pageSrc(r, index) {
        const v = this.state.layout?.v || "";
        return this.state.layout?.kind === "pdf"
            ? `/doc_search/page/${r.id}/${index + 1}?v=${v}`
            : `/web/image/${r.id}?unique=${v}`;
    }

    pageStyle(page) {
        const max = this.state.layout?.kind === "image" ? `max-width: ${page.w}px;` : "";
        return `aspect-ratio: ${page.w} / ${page.h}; ${max}`;
    }

    boxStyle(b) {
        const pct = (v) => `${(v * 100).toFixed(3)}%`;
        return `left: ${pct(b[0])}; top: ${pct(b[1])}; width: ${pct(b[2] - b[0])}; height: ${pct(b[3] - b[1])};`;
    }

    matchElements() {
        const el = this.state.tab === "text" ? this.previewText() : this.previewView();
        return el ? el.querySelectorAll(this.state.tab === "text" ? "mark" : ".o_ds_box") : [];
    }

    focusMatch(index) {
        const marks = this.matchElements();
        if (!marks.length) {
            return;
        }
        const i = (index + marks.length) % marks.length;
        marks.forEach((m) => m.classList.remove("o_ds_active"));
        marks[i].classList.add("o_ds_active");
        marks[i].scrollIntoView({ block: "center", behavior: "smooth" });
        this.state.matchIndex = i;
    }

    // ---------------------------------------------------------------- upload

    toggleUpload() {
        this.upload.open = !this.upload.open;
        // preselect the project when exactly one project filter is active
        if (this.upload.open && !this.upload.projectId && this.state.projectIds.length === 1) {
            this.upload.projectId = this.state.projectIds[0];
        }
    }

    setProject(id) {
        this.upload.projectId = id || false;
        this.upload.taskId = false;
    }

    setTask(id) {
        this.upload.taskId = id || false;
    }

    get taskDomain() {
        return [["project_id", "=", this.upload.projectId || 0]];
    }

    get uploadTarget() {
        if (this.upload.taskId) {
            return { model: "project.task", id: this.upload.taskId };
        }
        if (this.upload.projectId) {
            return { model: "project.project", id: this.upload.projectId };
        }
        return null;
    }

    openFilePicker() {
        if (!this.uploadTarget) {
            this.notification.add(_t("Please select a project first."), { type: "warning" });
            return;
        }
        this.fileInput()?.click();
    }

    onFileInput(ev) {
        this.uploadFiles([...ev.target.files]);
        ev.target.value = "";
    }

    onDragOver(ev) {
        ev.preventDefault();
        this.upload.dragOver = true;
    }

    onDragLeave() {
        this.upload.dragOver = false;
    }

    onDrop(ev) {
        ev.preventDefault();
        this.upload.dragOver = false;
        this.uploadFiles([...ev.dataTransfer.files]);
    }

    async uploadFiles(files) {
        const target = this.uploadTarget;
        if (!files.length) {
            return;
        }
        if (!target) {
            this.notification.add(_t("Please select a project first."), { type: "warning" });
            return;
        }
        this.upload.busy = true;
        for (const file of files) {
            this.upload.items.unshift({
                key: `${Date.now()}_${Math.random()}`,
                name: file.name,
                size: file.size,
                progress: 0,
                status: "uploading",
                id: null,
                error: "",
            });
            const item = this.upload.items[0]; // reactive proxy
            await this.uploadOne(item, file, target);
        }
        this.upload.busy = false;
        this.startPolling();
    }

    uploadOne(item, file, target) {
        return new Promise((resolve) => {
            const fd = new FormData();
            fd.append("csrf_token", odoo.csrf_token);
            fd.append("res_model", target.model);
            fd.append("res_id", target.id);
            fd.append("ufile", file);
            const xhr = new XMLHttpRequest();
            xhr.open("POST", "/doc_search/upload");
            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                    item.progress = Math.round((e.loaded * 100) / e.total);
                }
            };
            xhr.onload = () => {
                let data = {};
                try {
                    data = JSON.parse(xhr.responseText);
                } catch {
                    data = { error: `HTTP ${xhr.status}` };
                }
                if (xhr.status === 200 && data.id) {
                    Object.assign(item, { id: data.id, progress: 100, status: "indexing" });
                } else {
                    Object.assign(item, { status: "error", error: data.error || `HTTP ${xhr.status}` });
                }
                resolve();
            };
            xhr.onerror = () => {
                Object.assign(item, { status: "error", error: _t("Network error") });
                resolve();
            };
            xhr.send(fd);
        });
    }

    startPolling() {
        if (this._poll) {
            clearInterval(this._poll);
        }
        let rounds = 0;
        this._poll = setInterval(async () => {
            const pending = this.upload.items.filter((i) => i.status === "indexing");
            if (!pending.length || ++rounds > POLL_MAX) {
                clearInterval(this._poll);
                this._poll = null;
                return;
            }
            const states = await rpc("/doc_search/index_status", { ids: pending.map((i) => i.id) });
            let finished = 0;
            for (const item of pending) {
                const s = states[item.id];
                if (s === "done") {
                    item.status = "done";
                    finished++;
                } else if (s === "error") {
                    Object.assign(item, { status: "error", error: _t("Text extraction failed") });
                }
            }
            if (finished && this.state.searched) {
                // Solr commitWithin=3s -> give it a moment before refreshing
                setTimeout(() => this.search(this.state.page), POLL_MS);
            }
            if (!this.upload.items.some((i) => i.status === "indexing")) {
                this.notification.add(_t("Documents indexed — they are now searchable."), { type: "success" });
            }
        }, POLL_MS);
    }

    clearUploads() {
        this.upload.items = this.upload.items.filter((i) => i.status === "uploading" || i.status === "indexing");
    }

    formatSize(bytes) {
        if (bytes < 1024 * 1024) {
            return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        }
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    statusLabel(item) {
        return {
            uploading: _t("Uploading %s%%", item.progress),
            indexing: _t("Indexing…"),
            done: _t("Done"),
            error: item.error,
        }[item.status];
    }

    // ---------------------------------------------------------------- actions

    openRecord(r) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: r.res_model,
            res_id: r.res_id,
            views: [[false, "form"]],
        });
    }

    openProject(r) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.project",
            res_id: r.project_id,
            views: [[false, "form"]],
        });
    }

    formatDate(value) {
        return value ? new Date(value).toLocaleDateString() : "";
    }
}

registry.category("actions").add("ltech_doc_search.main", DocSearch);
