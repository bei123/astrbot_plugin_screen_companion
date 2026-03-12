const state = {
    isAuthenticated: false,
    requiresAuth: false,
    activeSection: "diaries",
    diaryDates: [],
    selectedDiaryDate: "",
    observations: [],
    selectedObservationIndices: new Set(),
    observationPage: 1,
    observationPages: 1,
    observationTotal: 0,
    observationLimit: 12,
    sceneFilter: "",
    sortFilter: "desc",
    dashboardRange: "30d",
    dashboardStartDate: "",
    dashboardEndDate: "",
    memories: [],
    dashboardStats: null,
    runtime: null,
    diaryObservationsExpanded: false,
    settingsSchema: {},
    settingsValues: {},
    settingsSnapshot: {},
    settingsGroups: [],
    activeSettingsGroup: "persona",
    settingsSearch: "",
};

const elements = {
    statusDot: document.getElementById("statusDot"),
    statusText: document.getElementById("statusText"),
    pluginVersion: document.getElementById("pluginVersion"),
    webuiVersion: document.getElementById("webuiVersion"),
    refreshButton: document.getElementById("refreshButton"),
    logoutButton: document.getElementById("logoutButton"),
    diaryCount: document.getElementById("diaryCount"),
    observationCount: document.getElementById("observationCount"),
    memoryCount: document.getElementById("memoryCount"),
    lastUpdated: document.getElementById("lastUpdated"),
    diaryList: document.getElementById("diaryList"),
    diaryReflection: document.getElementById("diaryReflection"),
    diaryObservations: document.getElementById("diaryObservations"),
    toggleDiaryObservations: document.getElementById("toggleDiaryObservations"),
    diaryTitle: document.getElementById("diaryTitle"),
    diaryMeta: document.getElementById("diaryMeta"),
    diarySummary: document.getElementById("diarySummary"),
    diaryDateInput: document.getElementById("diaryDateInput"),
    observationList: document.getElementById("observationList"),
    observationMeta: document.getElementById("observationMeta"),
    observationPagination: document.getElementById("observationPagination"),
    sceneFilter: document.getElementById("sceneFilter"),
    sortFilter: document.getElementById("sortFilter"),
    selectAllObservations: document.getElementById("selectAllObservations"),
    clearSelectionButton: document.getElementById("clearSelectionButton"),
    deleteSelectedButton: document.getElementById("deleteSelectedButton"),
    memoryHighlights: document.getElementById("memoryHighlights"),
    memoryGroups: document.getElementById("memoryGroups"),
    statsTables: document.getElementById("statsTables"),
    statsRangeFilter: document.getElementById("statsRangeFilter"),
    statsStartDateField: document.getElementById("statsStartDateField"),
    statsEndDateField: document.getElementById("statsEndDateField"),
    statsStartDateInput: document.getElementById("statsStartDateInput"),
    statsEndDateInput: document.getElementById("statsEndDateInput"),
    applyStatsRangeButton: document.getElementById("applyStatsRangeButton"),
    loginOverlay: document.getElementById("loginOverlay"),
    loginForm: document.getElementById("loginForm"),
    loginPassword: document.getElementById("loginPassword"),
    loginError: document.getElementById("loginError"),
    runtimeMeta: document.getElementById("runtimeMeta"),
    runtimeStats: document.getElementById("runtimeStats"),
    runtimeForm: document.getElementById("runtimeForm"),
    runtimeFeedback: document.getElementById("runtimeFeedback"),
    enabledSelect: document.getElementById("enabledSelect"),
    presetSelect: document.getElementById("presetSelect"),
    checkIntervalInput: document.getElementById("checkIntervalInput"),
    triggerProbabilityInput: document.getElementById("triggerProbabilityInput"),
    interactionFrequencyInput: document.getElementById("interactionFrequencyInput"),
    enableDiarySelect: document.getElementById("enableDiarySelect"),
    enableLearningSelect: document.getElementById("enableLearningSelect"),
    enableMicMonitorSelect: document.getElementById("enableMicMonitorSelect"),
    debugSelect: document.getElementById("debugSelect"),
    stopTasksButton: document.getElementById("stopTasksButton"),
    settingsSummary: document.getElementById("settingsSummary"),
    settingsGroupList: document.getElementById("settingsGroupList"),
    settingsGroupTitle: document.getElementById("settingsGroupTitle"),
    settingsGroupDescription: document.getElementById("settingsGroupDescription"),
    settingsSearchInput: document.getElementById("settingsSearchInput"),
    settingsForm: document.getElementById("settingsForm"),
    settingsFeedback: document.getElementById("settingsFeedback"),
    saveSettingsButton: document.getElementById("saveSettingsButton"),
    resetSettingsButton: document.getElementById("resetSettingsButton"),
    emptyStateTemplate: document.getElementById("emptyStateTemplate"),
    navLinks: Array.from(document.querySelectorAll(".nav-link")),
    sections: Array.from(document.querySelectorAll(".section")),
};

function getLocalDateString(offsetDays = 0) {
    const date = new Date();
    date.setDate(date.getDate() + offsetDays);
    const year = date.getFullYear();
    const month = `${date.getMonth() + 1}`.padStart(2, "0");
    const day = `${date.getDate()}`.padStart(2, "0");
    return `${year}-${month}-${day}`;
}

state.dashboardStartDate = getLocalDateString(-29);
state.dashboardEndDate = getLocalDateString(0);

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function cloneEmptyState() {
    return elements.emptyStateTemplate.content.firstElementChild.cloneNode(true);
}

function setConnectionState(type, text) {
    elements.statusDot.className = "status-dot";
    if (type) elements.statusDot.classList.add(type);
    elements.statusText.textContent = text;
}

function formatDateTime(value) {
    if (!value) return "未知时间";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    }).format(date);
}

function formatDateLabel(value) {
    if (!value) return "未指定日期";
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
        weekday: "short",
    }).format(date);
}

async function apiFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
    }

    const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers,
    });

    let payload = {};
    try {
        payload = await response.json();
    } catch (error) {
        payload = {};
    }

    if (response.status === 401) {
        state.isAuthenticated = false;
        if (state.requiresAuth) showLoginForm("登录已失效，请重新输入密码。");
    }

    if (!response.ok || payload.success === false) {
        throw new Error(payload.error || `请求失败 (${response.status})`);
    }

    return payload;
}

function showLoginForm(message = "") {
    elements.loginOverlay.classList.remove("hidden");
    elements.loginOverlay.setAttribute("aria-hidden", "false");
    elements.loginError.textContent = message;
    elements.loginPassword.value = "";
    window.setTimeout(() => elements.loginPassword.focus(), 30);
}

function hideLoginForm() {
    elements.loginOverlay.classList.add("hidden");
    elements.loginOverlay.setAttribute("aria-hidden", "true");
    elements.loginError.textContent = "";
}

function renderLoading(target, text = "正在加载…") {
    target.innerHTML = `<div class="empty-state"><strong>${escapeHtml(text)}</strong></div>`;
}

function syncDashboardRangeControls() {
    const isCustom = state.dashboardRange === "custom";
    elements.statsRangeFilter.value = state.dashboardRange;
    elements.statsStartDateField.classList.toggle("hidden", !isCustom);
    elements.statsEndDateField.classList.toggle("hidden", !isCustom);
    elements.applyStatsRangeButton.classList.toggle("hidden", !isCustom);
    elements.statsStartDateInput.value = state.dashboardStartDate || "";
    elements.statsEndDateInput.value = state.dashboardEndDate || "";
}

function renderTableCard({ title, subtitle = "", columns = [], rows = [], wide = false, emptyText = "暂无统计数据。" }) {
    const article = document.createElement("article");
    article.className = "panel table-card";
    if (wide) article.classList.add("table-card-wide");

    const header = document.createElement("div");
    header.className = "panel-header";
    header.innerHTML = `
        <h3>${escapeHtml(title)}</h3>
        <span class="panel-subtle">${escapeHtml(subtitle)}</span>
    `;
    article.appendChild(header);

    if (!rows.length) {
        const empty = cloneEmptyState();
        const hint = empty.querySelector("p");
        if (hint) hint.textContent = emptyText;
        article.appendChild(empty);
        return article;
    }

    const tableWrapper = document.createElement("div");
    tableWrapper.className = "data-table-wrapper";

    const table = document.createElement("table");
    table.className = "data-table";

    const thead = document.createElement("thead");
    thead.innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>`;

    const tbody = document.createElement("tbody");
    tbody.innerHTML = rows
        .map(
            (row) => `
                <tr>
                    ${columns
                        .map((column) => {
                            const className = column.numeric ? "data-table-numeric" : "";
                            return `<td class="${className}">${escapeHtml(row[column.key] ?? "")}</td>`;
                        })
                        .join("")}
                </tr>
            `
        )
        .join("");

    table.append(thead, tbody);
    tableWrapper.appendChild(table);
    article.appendChild(tableWrapper);
    return article;
}

function switchSection(sectionId) {
    state.activeSection = sectionId;
    elements.navLinks.forEach((link) => {
        link.classList.toggle("active", link.dataset.section === sectionId);
    });
    elements.sections.forEach((section) => {
        section.classList.toggle("active", section.id === sectionId);
    });
}

function updateSummaryCards() {
    elements.diaryCount.textContent = String(state.diaryDates.length);
    elements.observationCount.textContent = String(state.observationTotal);
    elements.memoryCount.textContent = String(state.memories.length);
    elements.lastUpdated.textContent = new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(new Date());
}

function getSettingMeta(key) {
    return state.settingsSchema[key] || {};
}

function getVisibleSettingsGroups() {
    const query = state.settingsSearch.trim().toLowerCase();
    if (!query) return state.settingsGroups;

    return state.settingsGroups
        .map((group) => {
            const fields = (group.fields || []).filter((fieldKey) => {
                const meta = getSettingMeta(fieldKey);
                const haystacks = [
                    fieldKey,
                    meta.description || "",
                    meta.hint || "",
                ];
                return haystacks.some((item) => String(item).toLowerCase().includes(query));
            });
            return { ...group, fields };
        })
        .filter((group) => group.fields.length > 0);
}

function shouldShowSettingField(fieldKey, currentValues) {
    const meta = getSettingMeta(fieldKey);
    const condition = meta.condition || {};
    return Object.entries(condition).every(([key, expected]) => currentValues[key] === expected);
}

function createSettingsInput(fieldKey, meta, value) {
    const type = meta.type || "string";

    if (type === "bool") {
        const select = document.createElement("select");
        select.dataset.settingKey = fieldKey;
        select.innerHTML = `
            <option value="true">开启</option>
            <option value="false">关闭</option>
        `;
        select.value = value ? "true" : "false";
        return select;
    }

    if (meta.enum && Array.isArray(meta.enum)) {
        const select = document.createElement("select");
        select.dataset.settingKey = fieldKey;
        select.innerHTML = meta.enum
            .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
            .join("");
        select.value = String(value ?? meta.default ?? "");
        return select;
    }

    const input = document.createElement(meta.type === "text" ? "textarea" : "input");
    input.dataset.settingKey = fieldKey;

    if (meta.type === "text") {
        input.rows = Math.min(12, Math.max(4, String(value ?? meta.default ?? "").split("\n").length + 1));
    } else {
        input.type = meta.type === "password" ? "password" : meta.type === "int" ? "number" : "text";
        if (meta.type === "int") {
            if (meta.min !== undefined) input.min = String(meta.min);
            if (meta.max !== undefined) input.max = String(meta.max);
            input.step = "1";
        }
    }

    input.value = String(value ?? meta.default ?? "");
    return input;
}

function readSettingInputValue(input, meta) {
    if (meta.type === "bool") {
        return input.value === "true";
    }
    if (meta.type === "int") {
        return Number(input.value || 0);
    }
    return input.value;
}

function renderSettingsGroups() {
    const visibleGroups = getVisibleSettingsGroups();
    elements.settingsGroupList.innerHTML = "";
    elements.settingsSummary.textContent = visibleGroups.length
        ? `当前可见 ${visibleGroups.length} 个配置分组。`
        : "没有匹配到配置项。";

    if (!visibleGroups.length) {
        elements.settingsGroupList.appendChild(cloneEmptyState());
        return;
    }

    if (!visibleGroups.some((group) => group.id === state.activeSettingsGroup)) {
        state.activeSettingsGroup = visibleGroups[0].id;
    }

    visibleGroups.forEach((group) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "settings-group-button";
        if (group.id === state.activeSettingsGroup) button.classList.add("active");
        button.innerHTML = `
            <strong>${escapeHtml(group.title)}</strong>
            <span>${escapeHtml(group.description || "")}</span>
        `;
        button.addEventListener("click", () => {
            state.activeSettingsGroup = group.id;
            renderSettingsGroups();
            renderSettingsForm();
        });
        elements.settingsGroupList.appendChild(button);
    });
}

function renderSettingsForm() {
    const visibleGroups = getVisibleSettingsGroups();
    const activeGroup = visibleGroups.find((group) => group.id === state.activeSettingsGroup);

    elements.settingsForm.innerHTML = "";
    if (!activeGroup) {
        elements.settingsGroupTitle.textContent = "没有匹配结果";
        elements.settingsGroupDescription.textContent = "换个关键词试试，或者清空筛选。";
        elements.settingsForm.appendChild(cloneEmptyState());
        return;
    }

    elements.settingsGroupTitle.textContent = activeGroup.title;
    elements.settingsGroupDescription.textContent = activeGroup.description || "编辑后点击保存即可写回插件配置。";

    const currentValues = { ...state.settingsValues };
    const visibleFields = activeGroup.fields.filter((fieldKey) => shouldShowSettingField(fieldKey, currentValues));

    if (!visibleFields.length) {
        const empty = cloneEmptyState();
        empty.querySelector("strong").textContent = "当前分组没有可编辑项";
        empty.querySelector("p").textContent = "可能是被前置条件隐藏了，也可能是筛选词过于严格。";
        elements.settingsForm.appendChild(empty);
        return;
    }

    visibleFields.forEach((fieldKey) => {
        const meta = getSettingMeta(fieldKey);
        const wrapper = document.createElement("label");
        wrapper.className = meta.type === "text" ? "field settings-field settings-field-wide" : "field settings-field";

        const header = document.createElement("div");
        header.className = "settings-field-header";
        header.innerHTML = `
            <strong>${escapeHtml(meta.description || fieldKey)}</strong>
            <code>${escapeHtml(fieldKey)}</code>
        `;

        const hint = document.createElement("p");
        hint.className = "settings-field-hint";
        hint.textContent = meta.hint || "未提供额外说明。";

        const input = createSettingsInput(fieldKey, meta, currentValues[fieldKey]);
        input.addEventListener("change", () => {
            state.settingsValues[fieldKey] = readSettingInputValue(input, meta);
            renderSettingsForm();
        });

        wrapper.append(header, hint, input);
        elements.settingsForm.appendChild(wrapper);
    });
}

function renderInlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderDiaryMarkdown(content) {
    if (!content) return "";

    const lines = String(content).replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];
    let listItems = [];
    let codeLines = [];
    let inCodeBlock = false;

    function flushParagraph() {
        if (!paragraph.length) return;
        blocks.push(`<p>${renderInlineMarkdown(paragraph.join("<br>"))}</p>`);
        paragraph = [];
    }

    function flushList() {
        if (!listItems.length) return;
        blocks.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
        listItems = [];
    }

    function flushCode() {
        if (!codeLines.length) return;
        blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
    }

    for (const rawLine of lines) {
        const line = rawLine.trimEnd();

        if (line.startsWith("```")) {
            flushParagraph();
            flushList();
            if (inCodeBlock) {
                flushCode();
                inCodeBlock = false;
            } else {
                inCodeBlock = true;
            }
            continue;
        }

        if (inCodeBlock) {
            codeLines.push(rawLine);
            continue;
        }

        if (!line.trim()) {
            flushParagraph();
            flushList();
            continue;
        }

        if (line.startsWith("# ")) {
            flushParagraph();
            flushList();
            blocks.push(`<h1>${renderInlineMarkdown(line.slice(2))}</h1>`);
            continue;
        }
        if (line.startsWith("## ")) {
            flushParagraph();
            flushList();
            blocks.push(`<h2>${renderInlineMarkdown(line.slice(3))}</h2>`);
            continue;
        }
        if (line.startsWith("### ")) {
            flushParagraph();
            flushList();
            blocks.push(`<h3>${renderInlineMarkdown(line.slice(4))}</h3>`);
            continue;
        }
        if (line.startsWith("> ")) {
            flushParagraph();
            flushList();
            blocks.push(`<blockquote>${renderInlineMarkdown(line.slice(2))}</blockquote>`);
            continue;
        }
        if (/^[-*] /.test(line)) {
            flushParagraph();
            listItems.push(line.slice(2));
            continue;
        }

        paragraph.push(renderInlineMarkdown(line));
    }

    flushParagraph();
    flushList();
    flushCode();
    return `<div class="diary-rendered">${blocks.join("")}</div>`;
}

function parseDiaryObservationEntries(content) {
    const text = String(content || "").replace(/\r\n/g, "\n").trim();
    if (!text) return [];

    const lines = text.split("\n");
    const entries = [];
    let current = null;

    function pushCurrent() {
        if (!current) return;
        current.body = current.body.map((line) => line.trimEnd()).join("\n").trim();
        entries.push(current);
        current = null;
    }

    for (const rawLine of lines) {
        const line = rawLine.trimEnd();
        const match = line.match(/^###\s+(\d{2}:\d{2}(?::\d{2})?)\s*-\s*(.+)$/);
        if (match) {
            pushCurrent();
            current = {
                time: match[1],
                windowTitle: match[2].trim(),
                body: [],
            };
            continue;
        }

        if (!current) {
            return [];
        }
        current.body.push(rawLine);
    }

    pushCurrent();
    return entries.filter((entry) => entry.body && entry.body.trim());
}

function renderDiaryObservationTimeline(entries) {
    if (!entries.length) return "";

    const items = entries.map((entry) => {
        const bodyHtml = renderDiaryMarkdown(entry.body)
            .replace('<div class="diary-rendered">', '<div class="diary-rendered diary-entry-body">');

        return `
            <article class="diary-observation-entry">
                <div class="diary-observation-marker" aria-hidden="true"></div>
                <div class="diary-observation-main">
                    <div class="diary-observation-head">
                        <span class="diary-observation-time">${escapeHtml(entry.time)}</span>
                        <span class="diary-observation-window">${escapeHtml(entry.windowTitle)}</span>
                    </div>
                    ${bodyHtml}
                </div>
            </article>
        `;
    });

    return `<div class="diary-observation-timeline">${items.join("")}</div>`;
}

function splitDiaryContent(content) {
    const text = String(content || "");
    const sections = {
        full: text.trim(),
        observation: "",
        reflection: "",
    };

    if (!text.trim()) {
        return sections;
    }

    const observationMatch = text.match(/##\s*今日观察\s*([\s\S]*?)(?=\n##\s*今日感想|\n##\s*[^\n]+|$)/);
    const reflectionMatch = text.match(/##\s*今日感想\s*([\s\S]*?)(?=\n##\s*[^\n]+|$)/);

    sections.observation = (observationMatch?.[1] || "").trim();
    sections.reflection = (reflectionMatch?.[1] || "").trim();

    if (!sections.reflection) {
        sections.reflection = text.trim();
    }

    return sections;
}

function renderDiaryList() {
    elements.diaryList.innerHTML = "";
    if (state.diaryDates.length === 0) {
        elements.diaryList.appendChild(cloneEmptyState());
        elements.diarySummary.textContent = "还没有生成任何日记。";
        return;
    }

    elements.diarySummary.textContent = `共 ${state.diaryDates.length} 篇日记，默认打开最近日期。`;
    state.diaryDates.slice(0, 14).forEach((entry) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-item-button";
        if (entry.date === state.selectedDiaryDate) button.classList.add("active");
        button.innerHTML = `
            <p class="list-item-title">${escapeHtml(formatDateLabel(entry.date))}</p>
            <p class="list-item-meta">文件名: ${escapeHtml(entry.filename)}</p>
        `;
        button.addEventListener("click", () => {
            elements.diaryDateInput.value = entry.date;
            loadDiaryDetail(entry.date);
        });
        elements.diaryList.appendChild(button);
    });
}

function renderDiaryDetail(date, content) {
    state.selectedDiaryDate = date;
    elements.diaryDateInput.value = date || "";
    renderDiaryList();
    elements.diaryTitle.textContent = date ? `${formatDateLabel(date)} 的日记` : "日记内容";
    elements.diaryMeta.textContent = content ? "已加载完整内容" : "这一天还没有写入内容";

    if (!content) {
        state.diaryObservationsExpanded = false;
        elements.toggleDiaryObservations.textContent = "展开";
        elements.toggleDiaryObservations.disabled = true;
        const empty = cloneEmptyState();
        empty.querySelector("strong").textContent = "这一天还没有日记";
        empty.querySelector("p").textContent = "等插件在当天生成记录后，这里会显示完整内容。";
        elements.diaryReflection.innerHTML = "";
        elements.diaryReflection.appendChild(empty);
        elements.diaryObservations.innerHTML = "";
        elements.diaryObservations.appendChild(cloneEmptyState());
        return;
    }

    const diary = splitDiaryContent(content);
    elements.diaryReflection.innerHTML = renderDiaryMarkdown(diary.reflection || diary.full);

    if (diary.observation) {
        const structuredEntries = parseDiaryObservationEntries(diary.observation);
        elements.diaryObservations.innerHTML = structuredEntries.length
            ? renderDiaryObservationTimeline(structuredEntries)
            : renderDiaryMarkdown(diary.observation);
        state.diaryObservationsExpanded = false;
        elements.diaryObservations.classList.add("diary-content-collapsed");
        elements.toggleDiaryObservations.disabled = false;
        elements.toggleDiaryObservations.textContent = "展开";
    } else {
        const emptyObservation = cloneEmptyState();
        emptyObservation.querySelector("strong").textContent = "今天没有单独整理观察段落";
        emptyObservation.querySelector("p").textContent = "如果后续日记模板保持“今日观察”标题，这里会自动拆分展示。";
        elements.diaryObservations.innerHTML = "";
        elements.diaryObservations.appendChild(emptyObservation);
        elements.diaryObservations.classList.remove("diary-content-collapsed");
        elements.toggleDiaryObservations.disabled = true;
        elements.toggleDiaryObservations.textContent = "展开";
    }
}

function syncObservationSelectionUi() {
    const visibleIndices = state.observations.map((item) => item.index);
    const selectedVisibleCount = visibleIndices.filter((index) => state.selectedObservationIndices.has(index)).length;
    elements.selectAllObservations.checked = Boolean(visibleIndices.length) && selectedVisibleCount === visibleIndices.length;
    elements.deleteSelectedButton.textContent = selectedVisibleCount
        ? `删除选中（${selectedVisibleCount}）`
        : "删除选中";
}

function renderObservationPagination() {
    elements.observationPagination.innerHTML = "";

    const summary = document.createElement("span");
    summary.className = "panel-subtle";
    summary.textContent = state.observationTotal
        ? `当前显示 ${state.observations.length} 条，已选 ${state.selectedObservationIndices.size} 条`
        : "暂无可分页内容";

    const controls = document.createElement("div");
    controls.className = "toolbar";

    const prevButton = document.createElement("button");
    prevButton.type = "button";
    prevButton.className = "page-button";
    prevButton.textContent = "上一页";
    prevButton.disabled = state.observationPage <= 1;
    prevButton.addEventListener("click", async () => {
        state.observationPage -= 1;
        await loadObservations();
    });

    const nextButton = document.createElement("button");
    nextButton.type = "button";
    nextButton.className = "page-button";
    nextButton.textContent = "下一页";
    nextButton.disabled = state.observationPage >= state.observationPages;
    nextButton.addEventListener("click", async () => {
        state.observationPage += 1;
        await loadObservations();
    });

    controls.append(prevButton, nextButton);
    elements.observationPagination.append(summary, controls);
}

async function deleteObservation(index) {
    await apiFetch(`/api/observations/${index}`, { method: "DELETE" });
    state.selectedObservationIndices.delete(index);
    await loadRuntime();
    await loadObservations();
    updateSummaryCards();
}

async function deleteSelectedObservations() {
    const indices = Array.from(state.selectedObservationIndices);
    if (!indices.length) return;
    await apiFetch("/api/observations/batch", {
        method: "DELETE",
        body: JSON.stringify({ indices }),
    });
    state.selectedObservationIndices.clear();
    await loadRuntime();
    await loadObservations();
    updateSummaryCards();
}

function renderObservationList() {
    elements.observationList.innerHTML = "";
    if (state.observations.length === 0) {
        elements.observationList.appendChild(cloneEmptyState());
        elements.observationMeta.textContent = "当前筛选条件下没有观察记录。";
        syncObservationSelectionUi();
        renderObservationPagination();
        return;
    }

    elements.observationMeta.textContent = `第 ${state.observationPage} / ${state.observationPages} 页，共 ${state.observationTotal} 条观察记录。`;

    state.observations.forEach((observation) => {
        const card = document.createElement("article");
        card.className = "observation-card";
        const selected = state.selectedObservationIndices.has(observation.index);
        const tags = [
            observation.scene ? `<span class="tag">${escapeHtml(observation.scene)}</span>` : "",
            observation.active_window ? `<span class="tag">${escapeHtml(observation.active_window)}</span>` : "",
        ].filter(Boolean).join("");

        card.innerHTML = `
            <div class="observation-header">
                <div>
                    <h3 class="list-item-title">${escapeHtml(formatDateTime(observation.timestamp))}</h3>
                    <div class="observation-tags">${tags || "未标注场景"}</div>
                </div>
                <label class="observation-select">
                    <input type="checkbox" ${selected ? "checked" : ""}>
                    <span>选择</span>
                </label>
            </div>
            <p class="observation-content">${escapeHtml(observation.content || observation.recognition || "暂无内容")}</p>
            <div class="observation-footer">
                <span class="panel-subtle">索引 ${escapeHtml(observation.index)}</span>
                <button class="danger-button" type="button">删除这条</button>
            </div>
        `;

        const checkbox = card.querySelector('input[type="checkbox"]');
        checkbox.addEventListener("change", () => {
            if (checkbox.checked) state.selectedObservationIndices.add(observation.index);
            else state.selectedObservationIndices.delete(observation.index);
            syncObservationSelectionUi();
            renderObservationPagination();
        });

        const deleteButton = card.querySelector(".danger-button");
        deleteButton.addEventListener("click", async () => {
            deleteButton.disabled = true;
            try {
                await deleteObservation(observation.index);
            } catch (error) {
                deleteButton.disabled = false;
                elements.observationMeta.textContent = `删除失败: ${error.message}`;
            }
        });

        elements.observationList.appendChild(card);
    });

    syncObservationSelectionUi();
    renderObservationPagination();
}

function renderSceneOptions(observations) {
    const previousValue = state.sceneFilter;
    const scenes = [...new Set((observations || []).map((item) => item.scene).filter(Boolean))];
    elements.sceneFilter.innerHTML = '<option value="">全部场景</option>';
    scenes.forEach((scene) => {
        const option = document.createElement("option");
        option.value = scene;
        option.textContent = scene;
        elements.sceneFilter.appendChild(option);
    });
    elements.sceneFilter.value = scenes.includes(previousValue) ? previousValue : "";
    state.sceneFilter = elements.sceneFilter.value;
}

function renderMemories() {
    elements.memoryHighlights.innerHTML = "";
    elements.memoryGroups.innerHTML = "";
    if (state.memories.length === 0) {
        elements.memoryHighlights.appendChild(cloneEmptyState());
        return;
    }

    [...state.memories]
        .sort((a, b) => (b.priority || 0) - (a.priority || 0))
        .slice(0, 3)
        .forEach((item) => {
            const highlight = document.createElement("article");
            highlight.className = "highlight-card";
            highlight.innerHTML = `
                <p class="panel-label">${escapeHtml(item.category_label)}</p>
                <strong>${escapeHtml(item.title)}</strong>
                <p class="memory-content">${escapeHtml(item.summary)}</p>
            `;
            elements.memoryHighlights.appendChild(highlight);
        });

    const groups = new Map();
    state.memories.forEach((item) => {
        if (!groups.has(item.category_label)) groups.set(item.category_label, []);
        groups.get(item.category_label).push(item);
    });

    groups.forEach((items, categoryLabel) => {
        const panel = document.createElement("article");
        panel.className = "panel memory-card";
        const list = items
            .sort((a, b) => (b.priority || 0) - (a.priority || 0))
            .slice(0, 8)
            .map((item) => `
                <div>
                    <div class="memory-header">
                        <strong>${escapeHtml(item.title)}</strong>
                        <span class="tag">优先级 ${escapeHtml(item.priority ?? 0)}</span>
                    </div>
                    <p class="memory-content">${escapeHtml(item.summary)}</p>
                    <p class="memory-meta">${escapeHtml(item.meta || "")}</p>
                </div>
            `)
            .join("");
        panel.innerHTML = `
            <div class="panel-header">
                <h3>${escapeHtml(categoryLabel)}</h3>
                <span class="panel-subtle">${items.length} 条记忆</span>
            </div>
            <div class="memory-list">${list}</div>
        `;
        elements.memoryGroups.appendChild(panel);
    });
}

function renderDashboard() {
    elements.statsTables.innerHTML = "";
    const dashboard = state.dashboardStats;
    if (!dashboard) {
        syncDashboardRangeControls();
        elements.statsTables.appendChild(cloneEmptyState());
        return;
    }

    if (dashboard.range_key && elements.statsRangeFilter) {
        state.dashboardRange = dashboard.range_key;
    }
    if (dashboard.range_start_date) {
        state.dashboardStartDate = dashboard.range_start_date;
    }
    if (dashboard.range_end_date) {
        state.dashboardEndDate = dashboard.range_end_date;
    }
    syncDashboardRangeControls();

    const generatedAt = dashboard.generated_at ? `更新于 ${formatDateTime(dashboard.generated_at)}` : "等待刷新";
    const rangeLabel = dashboard.range_label || "当前范围";

    const tables = [
        {
            title: "总览统计",
            subtitle: `${rangeLabel} · ${generatedAt}`,
            columns: [
                { key: "metric", label: "指标" },
                { key: "value", label: "数值", numeric: true },
                { key: "detail", label: "说明" },
            ],
            rows: dashboard.overview_rows || [],
        },
        {
            title: "活动时间汇总",
            subtitle: `基于 ${rangeLabel} 汇总活动时间`,
            columns: [
                { key: "range", label: "范围" },
                { key: "work", label: "工作" },
                { key: "play", label: "摸鱼" },
                { key: "other", label: "其他" },
                { key: "total", label: "总计" },
            ],
            rows: dashboard.activity_rows || [],
        },
        {
            title: "观察场景分布",
            subtitle: `${rangeLabel} 内已保留观察记录的聚合结果`,
            columns: [
                { key: "scene", label: "场景" },
                { key: "count", label: "次数", numeric: true },
                { key: "last_seen", label: "最近出现" },
                { key: "time_period", label: "时段" },
                { key: "window", label: "最近窗口" },
            ],
            rows: dashboard.scene_rows || [],
            wide: true,
        },
        {
            title: "记忆分类统计",
            subtitle: `${rangeLabel} 内仍然活跃的长期记忆`,
            columns: [
                { key: "category", label: "分类" },
                { key: "count", label: "条数", numeric: true },
                { key: "max_priority", label: "最高优先级", numeric: true },
                { key: "example", label: "代表项" },
            ],
            rows: dashboard.memory_category_rows || [],
        },
        {
            title: "高优先级记忆排行",
            subtitle: `${rangeLabel} 内最值得被提取到上下文的长期记忆`,
            columns: [
                { key: "rank", label: "#" },
                { key: "title", label: "标题" },
                { key: "category", label: "分类" },
                { key: "priority", label: "优先级", numeric: true },
                { key: "summary", label: "摘要" },
            ],
            rows: dashboard.top_memory_rows || [],
            wide: true,
        },
        {
            title: "最近活动流水",
            subtitle: `${rangeLabel} 内最近 10 条已归档活动片段`,
            columns: [
                { key: "start", label: "开始时间" },
                { key: "end", label: "结束时间" },
                { key: "type", label: "类型" },
                { key: "scene", label: "场景" },
                { key: "window", label: "窗口" },
                { key: "duration", label: "时长" },
            ],
            rows: dashboard.recent_activity_rows || [],
            wide: true,
        },
    ];

    tables.forEach((tableConfig) => {
        elements.statsTables.appendChild(renderTableCard(tableConfig));
    });
}

function renderRuntime() {
    const runtime = state.runtime;
    elements.runtimeStats.innerHTML = "";
    if (!runtime) {
        elements.runtimeMeta.textContent = "尚未加载运行状态。";
        elements.runtimeStats.appendChild(cloneEmptyState());
        return;
    }

    elements.runtimeMeta.textContent = `状态: ${runtime.state || "unknown"} | 自动任务 ${runtime.active_task_count || 0} 个`;
    const cards = [
        ["插件状态", runtime.enabled ? "已启用" : "已关闭"],
        ["运行中", runtime.is_running ? "是" : "否"],
        ["当前模式", runtime.interaction_mode || "未设置"],
        ["生效间隔", `${runtime.current_check_interval || 0} 秒`],
        ["触发概率", `${runtime.current_trigger_probability || 0}%`],
        ["观察记录", `${runtime.observation_count || 0} 条`],
        ["日记功能", runtime.enable_diary ? "开启" : "关闭"],
        ["学习功能", runtime.enable_learning ? "开启" : "关闭"],
    ];

    cards.forEach(([label, value]) => {
        const item = document.createElement("div");
        item.className = "runtime-stat";
        item.innerHTML = `<span class="panel-label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>`;
        elements.runtimeStats.appendChild(item);
    });

    elements.enabledSelect.value = String(Boolean(runtime.enabled));
    elements.checkIntervalInput.value = runtime.check_interval ?? runtime.current_check_interval ?? 300;
    elements.triggerProbabilityInput.value = runtime.trigger_probability ?? runtime.current_trigger_probability ?? 30;
    elements.interactionFrequencyInput.value = runtime.interaction_frequency ?? 5;
    elements.enableDiarySelect.value = String(Boolean(runtime.enable_diary));
    elements.enableLearningSelect.value = String(Boolean(runtime.enable_learning));
    elements.enableMicMonitorSelect.value = String(Boolean(runtime.enable_mic_monitor));
    elements.debugSelect.value = String(Boolean(runtime.debug));

    elements.presetSelect.innerHTML = '<option value="-1">手动配置</option>';
    (runtime.presets || []).forEach((preset) => {
        const option = document.createElement("option");
        option.value = String(preset.index);
        option.textContent = `${preset.index}. ${preset.name} (${preset.check_interval}s / ${preset.trigger_probability}%)`;
        elements.presetSelect.appendChild(option);
    });
    elements.presetSelect.value = String(runtime.current_preset_index ?? -1);
}

async function loadConfig() {
    const data = await apiFetch("/api/config");
    elements.pluginVersion.textContent = data.plugin_version || "--";
    elements.webuiVersion.textContent = data.version || "--";
}

async function loadRuntime() {
    const data = await apiFetch("/api/runtime");
    state.runtime = data.runtime || null;
    renderRuntime();
}

async function loadDashboardStats() {
    renderLoading(elements.statsTables, "正在整理统计看板…");
    const query = new URLSearchParams({
        range: state.dashboardRange,
    });
    if (state.dashboardRange === "custom") {
        if (state.dashboardStartDate) query.set("start_date", state.dashboardStartDate);
        if (state.dashboardEndDate) query.set("end_date", state.dashboardEndDate);
    }
    const data = await apiFetch(`/api/dashboard?${query.toString()}`);
    state.dashboardStats = data || null;
    renderDashboard();
}

async function loadSettings() {
    const data = await apiFetch("/api/settings");
    const settings = data.settings || {};
    state.settingsSchema = settings.schema || {};
    state.settingsValues = settings.values || {};
    state.settingsSnapshot = { ...(settings.values || {}) };
    state.settingsGroups = settings.groups || [];

    if (!state.settingsGroups.some((group) => group.id === state.activeSettingsGroup)) {
        state.activeSettingsGroup = state.settingsGroups[0]?.id || "";
    }

    renderSettingsGroups();
    renderSettingsForm();
}

async function loadDiaries() {
    renderLoading(elements.diaryList, "正在整理日记列表…");
    const data = await apiFetch("/api/diaries");
    state.diaryDates = data.diaries || [];
    if (!state.selectedDiaryDate) {
        state.selectedDiaryDate = state.diaryDates[0]?.date || new Date().toISOString().slice(0, 10);
    }
    renderDiaryList();
    await loadDiaryDetail(state.selectedDiaryDate);
}

async function loadDiaryDetail(date) {
    state.selectedDiaryDate = date;
    elements.diaryTitle.textContent = "正在载入日记…";
    renderLoading(elements.diaryReflection, "正在读取日记内容…");
    renderLoading(elements.diaryObservations, "正在整理观察记录…");
    const data = await apiFetch(`/api/diary/${date}`);
    renderDiaryDetail(data.date, data.content || "");
}

async function loadObservationScenes() {
    const data = await apiFetch("/api/observations?limit=200&sort=desc");
    renderSceneOptions(data.observations || []);
}

async function loadObservations() {
    renderLoading(elements.observationList, "正在读取观察记录…");
    const query = new URLSearchParams({
        page: String(state.observationPage),
        limit: String(state.observationLimit),
        sort: state.sortFilter,
    });
    if (state.sceneFilter) query.set("scene", state.sceneFilter);
    const data = await apiFetch(`/api/observations?${query.toString()}`);
    state.observations = data.observations || [];
    state.observationPage = data.page || 1;
    state.observationPages = data.pages || 1;
    state.observationTotal = data.total || 0;

    const visibleIndices = new Set(state.observations.map((item) => item.index));
    state.selectedObservationIndices.forEach((index) => {
        if (!Number.isInteger(index)) state.selectedObservationIndices.delete(index);
    });
    renderObservationList();
}

async function loadMemories() {
    renderLoading(elements.memoryGroups, "正在检索长期记忆…");
    const data = await apiFetch("/api/memories");
    state.memories = data.memories || [];
    renderMemories();
}

async function refreshActiveSection() {
    await loadConfig();
    await loadDashboardStats();
    await loadRuntime();
    await loadSettings();
    await loadDiaries();
    await loadObservationScenes();
    await loadObservations();
    await loadMemories();
    updateSummaryCards();
}

function collectVisibleSettingsUpdates() {
    const updates = {};
    const inputs = elements.settingsForm.querySelectorAll("[data-setting-key]");
    inputs.forEach((input) => {
        const key = input.dataset.settingKey;
        const meta = getSettingMeta(key);
        updates[key] = readSettingInputValue(input, meta);
    });
    return updates;
}

async function initialize() {
    const authInfo = await apiFetch("/auth/info");
    state.requiresAuth = Boolean(authInfo.requires_auth);
    state.isAuthenticated = Boolean(authInfo.authenticated) || !state.requiresAuth;
    elements.logoutButton.classList.toggle("hidden", !state.requiresAuth);
    if (state.requiresAuth && !state.isAuthenticated) {
        setConnectionState("error", "当前 WebUI 已启用访问保护，请先登录。");
        showLoginForm();
        return;
    }
    hideLoginForm();
    setConnectionState("online", "WebUI 服务连接正常。");
    await refreshActiveSection();
}

function readRuntimeFormValues() {
    return {
        enabled: elements.enabledSelect.value === "true",
        current_preset_index: Number(elements.presetSelect.value),
        check_interval: Number(elements.checkIntervalInput.value),
        trigger_probability: Number(elements.triggerProbabilityInput.value),
        interaction_frequency: Number(elements.interactionFrequencyInput.value),
        enable_diary: elements.enableDiarySelect.value === "true",
        enable_learning: elements.enableLearningSelect.value === "true",
        enable_mic_monitor: elements.enableMicMonitorSelect.value === "true",
        debug: elements.debugSelect.value === "true",
    };
}

elements.navLinks.forEach((link) => {
    link.addEventListener("click", async (event) => {
        event.preventDefault();
        switchSection(link.dataset.section);
        history.replaceState(null, "", `#${link.dataset.section}`);
        await refreshActiveSection();
    });
});

elements.refreshButton.addEventListener("click", async () => {
    setConnectionState("online", "正在刷新数据…");
    try {
        await refreshActiveSection();
        setConnectionState("online", "数据已刷新。");
    } catch (error) {
        setConnectionState("error", `刷新失败: ${error.message}`);
    }
});

elements.diaryDateInput.addEventListener("change", async () => {
    if (elements.diaryDateInput.value) await loadDiaryDetail(elements.diaryDateInput.value);
});

elements.toggleDiaryObservations.addEventListener("click", () => {
    state.diaryObservationsExpanded = !state.diaryObservationsExpanded;
    elements.diaryObservations.classList.toggle("diary-content-collapsed", !state.diaryObservationsExpanded);
    elements.toggleDiaryObservations.textContent = state.diaryObservationsExpanded ? "收起" : "展开";
});

elements.sceneFilter.addEventListener("change", async () => {
    state.sceneFilter = elements.sceneFilter.value;
    state.observationPage = 1;
    await loadObservations();
    updateSummaryCards();
});

elements.sortFilter.addEventListener("change", async () => {
    state.sortFilter = elements.sortFilter.value;
    state.observationPage = 1;
    await loadObservations();
});

elements.statsRangeFilter.addEventListener("change", async () => {
    state.dashboardRange = elements.statsRangeFilter.value || "30d";
    syncDashboardRangeControls();
    if (state.dashboardRange !== "custom") {
        await loadDashboardStats();
    }
});

elements.statsStartDateInput.addEventListener("change", () => {
    state.dashboardStartDate = elements.statsStartDateInput.value || "";
});

elements.statsEndDateInput.addEventListener("change", () => {
    state.dashboardEndDate = elements.statsEndDateInput.value || "";
});

elements.applyStatsRangeButton.addEventListener("click", async () => {
    state.dashboardStartDate = elements.statsStartDateInput.value || "";
    state.dashboardEndDate = elements.statsEndDateInput.value || "";

    if (!state.dashboardStartDate || !state.dashboardEndDate) {
        setConnectionState("error", "请先选择完整的起止日期。");
        return;
    }
    if (state.dashboardStartDate > state.dashboardEndDate) {
        setConnectionState("error", "开始日期不能晚于结束日期。");
        return;
    }

    setConnectionState("online", "正在按自定义日期刷新统计…");
    try {
        await loadDashboardStats();
        setConnectionState("online", "统计看板已按自定义日期更新。");
    } catch (error) {
        setConnectionState("error", `刷新失败: ${error.message}`);
    }
});

elements.selectAllObservations.addEventListener("change", () => {
    if (elements.selectAllObservations.checked) {
        state.observations.forEach((item) => state.selectedObservationIndices.add(item.index));
    } else {
        state.observations.forEach((item) => state.selectedObservationIndices.delete(item.index));
    }
    renderObservationList();
});

elements.clearSelectionButton.addEventListener("click", () => {
    state.selectedObservationIndices.clear();
    renderObservationList();
});

elements.deleteSelectedButton.addEventListener("click", async () => {
    if (!state.selectedObservationIndices.size) {
        elements.observationMeta.textContent = "请先选择要删除的观察记录。";
        return;
    }
    try {
        await deleteSelectedObservations();
        elements.observationMeta.textContent = "已删除选中的观察记录。";
    } catch (error) {
        elements.observationMeta.textContent = `批量删除失败: ${error.message}`;
    }
});

elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.loginError.textContent = "";
    try {
        await apiFetch("/auth/login", {
            method: "POST",
            body: JSON.stringify({ password: elements.loginPassword.value }),
        });
        state.isAuthenticated = true;
        hideLoginForm();
        setConnectionState("online", "登录成功，正在加载数据。");
        await refreshActiveSection();
    } catch (error) {
        elements.loginError.textContent = `登录失败: ${error.message}`;
    }
});

elements.runtimeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.runtimeFeedback.textContent = "";
    try {
        const data = await apiFetch("/api/runtime/config", {
            method: "POST",
            body: JSON.stringify(readRuntimeFormValues()),
        });
        state.runtime = data.runtime || null;
        renderRuntime();
        elements.runtimeFeedback.textContent = "运行设置已保存。";
    } catch (error) {
        elements.runtimeFeedback.textContent = `保存失败: ${error.message}`;
    }
});

    elements.settingsSearchInput.addEventListener("input", () => {
    state.settingsSearch = elements.settingsSearchInput.value || "";
    renderSettingsGroups();
    renderSettingsForm();
});

elements.resetSettingsButton.addEventListener("click", () => {
    state.settingsValues = { ...state.settingsSnapshot };
    renderSettingsForm();
    elements.settingsFeedback.textContent = "当前分组已恢复为最近一次加载到的值。";
});

elements.saveSettingsButton.addEventListener("click", async () => {
    elements.settingsFeedback.textContent = "";
    try {
        const updates = collectVisibleSettingsUpdates();
        const data = await apiFetch("/api/settings", {
            method: "POST",
            body: JSON.stringify({ updates }),
        });
        const settings = data.settings || {};
        state.settingsSchema = settings.schema || state.settingsSchema;
        state.settingsValues = settings.values || state.settingsValues;
        state.settingsSnapshot = { ...(settings.values || state.settingsValues) };
        state.settingsGroups = settings.groups || state.settingsGroups;
        renderSettingsGroups();
        renderSettingsForm();
        await loadRuntime();
        elements.settingsFeedback.textContent = "配置已保存，相关运行态已同步刷新。";
    } catch (error) {
        elements.settingsFeedback.textContent = `保存失败: ${error.message}`;
    }
});

elements.stopTasksButton.addEventListener("click", async () => {
    elements.runtimeFeedback.textContent = "";
    try {
        const data = await apiFetch("/api/runtime/stop", { method: "POST" });
        state.runtime = data.runtime || null;
        renderRuntime();
        elements.runtimeFeedback.textContent = "当前自动任务已停止。";
    } catch (error) {
        elements.runtimeFeedback.textContent = `停止失败: ${error.message}`;
    }
});

elements.logoutButton.addEventListener("click", async () => {
    try {
        await apiFetch("/auth/logout", { method: "POST" });
    } catch (error) {
        console.error(error);
    }
    state.isAuthenticated = false;
    showLoginForm("已退出登录。");
    setConnectionState("error", "已退出登录，请重新输入密码。");
});

window.addEventListener("DOMContentLoaded", async () => {
    const hash = window.location.hash.replace("#", "");
    if (["stats", "runtime", "settings", "diaries", "observations", "memories"].includes(hash)) {
        switchSection(hash);
    }

    try {
        await initialize();
    } catch (error) {
        setConnectionState("error", `初始化失败: ${error.message}`);
    }
});
