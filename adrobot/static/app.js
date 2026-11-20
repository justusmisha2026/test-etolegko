"use strict";

const $ = (selector) => document.querySelector(selector);
let configuration;
let catalogs;
let snapshot;
let selectedStream;
let draft = [];
let editorLocked = false;
let pendingCreation = null;
let loading = false;
const labels = {complete: "Завершено", partial: "Частичный результат", uncertain: "Результат не определён", failed: "Не выполнено", running: "Выполняется / требует сверки"};

function notify(message, success = false) {
  const box = $("#notice");
  box.textContent = message;
  box.className = success ? "success" : "error";
  box.hidden = false;
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {"Content-Type": "application/json", "X-Adrobot-Request": "1"},
  });
  const data = await response.json();
  if (!response.ok) {
    const fields = data.errors?.map((error) => `${error.field}: ${error.message}`).join("; ");
    throw new Error(fields || data.detail || "Ошибка запроса");
  }
  return data;
}

function optionList(select, records, placeholder) {
  const previous = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  records.forEach((row) => select.add(new Option(`${row.name} · ${row.id}`, row.id)));
  if (records.some((row) => String(row.id) === previous)) select.value = previous;
}

function showPanel(id) {
  document.querySelectorAll(".panel").forEach((panel) => { panel.hidden = panel.id !== id; });
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("selected", tab.dataset.panel === id));
}

function canWrite() {
  return configuration?.connected && configuration.writes_enabled && !configuration.creation_error;
}

function lockCreation(locked) {
  $("#create-form").querySelectorAll("input, select").forEach((field) => { field.disabled = locked; });
  $("#create-submit").disabled = locked || !canWrite();
  $("#new-operation").hidden = !locked;
}

async function loadOperations() {
  const records = await api("/operations");
  const target = $("#operation-list");
  target.replaceChildren();
  if (!records.length) { target.textContent = "Операций пока нет."; return; }
  records.forEach((row) => {
    const item = document.createElement("div");
    item.className = "operation";
    const content = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${labels[row.status] || row.status}${row.campaign_id ? ` · Кампания #${row.campaign_id}` : ""}`;
    const details = document.createElement("p");
    details.textContent = `Шаг: ${row.step} · ${row.id}`;
    const error = document.createElement("p");
    error.textContent = row.error || "";
    content.append(title, details, error);
    item.append(content);
    if (row.status !== "complete") {
      const button = document.createElement("button");
      button.className = "secondary";
      button.textContent = "Сверить состояние";
      button.onclick = async () => {
        button.disabled = true;
        try {
          const result = await api(`/operations/${row.id}/reconcile`, {method: "POST"});
          notify(`${labels[result.status]}${result.error ? `: ${result.error}` : ""}`, result.status === "complete");
          await loadOperations();
        } catch (error) { notify(error.message); button.disabled = false; }
      };
      item.append(button);
      if (row.status === "partial" && canWrite()) {
        const resume = document.createElement("button");
        resume.textContent = "Продолжить создание";
        resume.onclick = async () => {
          resume.disabled = true;
          try {
            const result = await api(`/operations/${row.id}/resume`, {method: "POST"});
            notify(`${labels[result.status]}${result.error ? `: ${result.error}` : ""}`, result.status === "complete");
            await loadOperations();
            await loadCampaigns();
          } catch (error) { notify(error.message); resume.disabled = false; }
        };
        item.append(resume);
      }
    }
    target.append(item);
  });
}

async function loadCampaigns() {
  optionList($("#campaign-select"), await api("/campaigns"), "Выберите кампанию");
}

async function load() {
  if (loading) return;
  loading = true;
  $("#reload").disabled = true;
  try {
    configuration = await api("/status");
    $("#mode").textContent = configuration.demo ? "Демо · данные в памяти" : "Keitaro · серверный API";
    $("#prefix-hint").textContent = `Префикс названия: ${configuration.prefix || "не задан"}`;
    const warnings = [configuration.configuration_error, configuration.creation_error];
    if (configuration.demo) warnings.push("Демонстрационный режим: запросы в Keitaro не отправляются. Данные сбрасываются при перезапуске приложения.");
    else if (!configuration.writes_enabled) warnings.push("Запись выключена. Проверьте контракт стенда и включите её в серверной конфигурации.");
    $("#configuration").textContent = warnings.filter(Boolean).join(" ");
    $("#configuration").hidden = !$("#configuration").textContent;
    optionList($('[name="country"]'), await api("/countries"), "Выберите страну");
    await loadOperations();
    if (!configuration.connected) return;
    catalogs = await api("/catalogs");
    for (const [field, resource] of Object.entries({offer_id: "offers", domain_id: "domains", group_id: "groups", traffic_source_id: "traffic_sources"})) {
      optionList($(`[name="${field}"]`), catalogs[resource], catalogs[resource].length ? "Выберите значение" : "Нет доступных записей");
    }
    await loadCampaigns();
    if ($("#campaign-select").value) await loadEditor();
  } catch (error) { notify(error.message); }
  finally { loading = false; $("#reload").disabled = false; lockCreation(Boolean(pendingCreation)); }
}

$("#create-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!pendingCreation) {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    for (const key of ["offer_id", "domain_id", "group_id", "traffic_source_id"]) values[key] = Number(values[key]);
    pendingCreation = {...values, operation_id: crypto.randomUUID()};
    sessionStorage.setItem("adrobot-pending-creation", JSON.stringify(pendingCreation));
  }
  lockCreation(true);
  $("#new-operation").disabled = true;
  try {
    const result = await api("/campaigns", {method: "POST", body: JSON.stringify(pendingCreation)});
    if (result.status === "complete") sessionStorage.removeItem("adrobot-pending-creation");
    notify(result.status === "complete" ? `Кампания #${result.campaign_id} создана и проверена.` : `${labels[result.status]}: ${result.error || result.step}. Откройте журнал операций.`, result.status === "complete");
    await loadOperations();
    await loadCampaigns();
  } catch (error) { notify(`${error.message}. Перед новой операцией проверьте журнал.`); }
  finally { $("#new-operation").disabled = false; }
};

$("#new-operation").onclick = () => {
  pendingCreation = null;
  sessionStorage.removeItem("adrobot-pending-creation");
  $("#notice").hidden = true;
  lockCreation(false);
};

async function loadEditor() {
  $("#offers-editor").hidden = true;
  $("#stream-select").disabled = true;
  const id = $("#campaign-select").value;
  if (!id) return;
  try {
    const loaded = await api(`/campaigns/${id}`);
    if ($("#campaign-select").value !== id) return;
    snapshot = loaded;
    editorLocked = false;
    optionList($("#stream-select"), snapshot.streams, "Выберите поток");
    $("#stream-select").disabled = false;
    $("#editor-hint").textContent = snapshot.editable ? "Выберите поток с офферами. Географический поток остаётся без изменений." : "Просмотр: имя кампании не соответствует вашему префиксу.";
    if ($("#stream-select").value) selectStream();
  } catch (error) { notify(error.message); }
}

function selectStream() {
  selectedStream = snapshot.streams.find((row) => String(row.id) === $("#stream-select").value);
  draft = structuredClone(selectedStream?.offers || []);
  $("#offers-editor").hidden = !selectedStream;
  renderOffers();
}

function editable() {
  return canWrite() && configuration.editor_enabled && snapshot?.editable && selectedStream?.schema === "landings" && selectedStream.offers.length > 0 && !editorLocked;
}

function compositionChanged() {
  const original = selectedStream?.offers || [];
  return draft.length !== original.length || draft.some((row, index) => row.offer_id !== original[index].offer_id);
}

function redistribute() {
  if (!compositionChanged()) {
    draft = structuredClone(selectedStream.offers);
    return;
  }
  const active = draft.filter((row) => row.state === "active");
  const base = Math.floor(100 / active.length);
  const remainder = 100 % active.length;
  draft.forEach((row) => { row.share = 0; });
  active.forEach((row, index) => { row.share = base + Number(index >= active.length - remainder); });
}

function renderOffers() {
  const body = $("#offer-rows");
  body.replaceChildren();
  draft.forEach((row, index) => {
    const tr = document.createElement("tr");
    const offer = catalogs.offers.find((item) => item.id === row.offer_id);
    for (const value of [`${offer?.name || "Оффер"} · ${row.offer_id}`, row.state === "active" ? "Активен" : "Выключен", `${row.share}%`]) {
      const td = document.createElement("td"); td.textContent = value; tr.append(td);
    }
    const td = document.createElement("td");
    const remove = document.createElement("button");
    remove.textContent = "Удалить";
    remove.disabled = !editable() || draft.length === 1 || (row.state === "active" && draft.filter((item) => item.state === "active").length === 1);
    remove.onclick = () => { draft.splice(index, 1); redistribute(); renderOffers(); };
    td.append(remove); tr.append(td); body.append(tr);
  });
  optionList($("#add-offer-select"), catalogs.offers.filter((offer) => !draft.some((row) => row.offer_id === offer.id)), "Выберите оффер");
  $("#add-offer").disabled = !editable() || draft.length >= 100;
  $("#save-offers").disabled = !editable() || !compositionChanged();
  $("#share-total").textContent = `Сумма активных долей: ${draft.filter((row) => row.state === "active").reduce((sum, row) => sum + row.share, 0)}%`;
}

$("#add-offer").onclick = () => {
  const id = Number($("#add-offer-select").value);
  if (!id || !editable() || draft.some((row) => row.offer_id === id)) return;
  const original = selectedStream.offers.find((row) => row.offer_id === id);
  draft.push({offer_id: id, share: 0, state: original?.state ?? "active"}); redistribute(); renderOffers();
};

$("#save-offers").onclick = async () => {
  editorLocked = true;
  renderOffers();
  $("#campaign-select").disabled = true;
  $("#stream-select").disabled = true;
  try {
    snapshot = await api(`/campaigns/${snapshot.id}/streams/${selectedStream.id}/offers`, {method: "PUT", body: JSON.stringify({revision: snapshot.revision, offer_ids: draft.map((row) => row.offer_id)})});
    editorLocked = false;
    selectStream();
    notify(configuration.demo ? "Состав и доли сохранены в демонстрационном хранилище." : "Состав и доли сохранены и проверены в Keitaro.", true);
  } catch (error) { notify(`${error.message}. Обновите данные перед дальнейшим редактированием.`); }
  finally { $("#campaign-select").disabled = false; $("#stream-select").disabled = false; }
};

$("#campaign-select").onchange = loadEditor;
$("#stream-select").onchange = selectStream;
$("#reload").onclick = load;
document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => showPanel(tab.dataset.panel); });
try {
  pendingCreation = JSON.parse(sessionStorage.getItem("adrobot-pending-creation"));
  if (pendingCreation) notify("В этой вкладке есть предыдущая операция создания. Проверьте журнал перед новой операцией.");
} catch { sessionStorage.removeItem("adrobot-pending-creation"); }
load();
