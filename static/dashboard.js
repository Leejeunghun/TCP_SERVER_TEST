const clients = new Map();
const rowsMap = new Map(); // client_id -> row DOM refs

const tbody = document.getElementById("client-tbody");
const clientCountEl = document.getElementById("client-count");
const eventLog = document.getElementById("event-log");
const connIndicator = document.getElementById("conn-indicator");

const STATUS_LABEL = { connected: "연결됨", idle: "응답없음", disconnected: "끊김" };
const STATUS_BADGE = { connected: "badge-green", idle: "badge-yellow", disconnected: "badge-red" };

const socket = io();

socket.on("connect", () => {
  connIndicator.textContent = "웹소켓 연결됨";
  connIndicator.className = "badge badge-green";
});

socket.on("disconnect", () => {
  connIndicator.textContent = "웹소켓 끊김";
  connIndicator.className = "badge badge-red";
});

socket.on("snapshot", (list) => {
  for (const id of rowsMap.keys()) removeRow(id);
  list
    .slice()
    .sort((a, b) => (a.connect_time < b.connect_time ? -1 : 1))
    .forEach(upsert);
});

socket.on("client_connected", (c) => {
  upsert(c);
  pushEvent("connected", c, `연결됨 (${c.ip}:${c.port})`);
});

socket.on("client_update", (c) => {
  upsert(c);
  const last = c.log[c.log.length - 1];
  if (last) {
    pushEvent("connected", c, `${last.dir} ${last.preview.len}B  ${last.preview.text.trim() || last.preview.hex}`);
  }
});

socket.on("client_idle", (c) => {
  upsert(c);
  pushEvent("idle", c, `응답없음 (${window.IDLE_TIMEOUT || "?"}s 이상 무통신)`);
});

socket.on("client_disconnected", (c) => {
  upsert(c);
  pushEvent("disconnected", c, `연결 종료`);
});

function fmtBytes(n) {
  if (n < 1024) return `${n}B`;
  return `${(n / 1024).toFixed(1)}KB`;
}

function idleText(c) {
  if (c.status === "disconnected") return "-";
  return Math.max(0, Math.floor(Date.now() / 1000 - c.last_seen_epoch)) + "s";
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
}

function renderLog(c) {
  return c.log
    .slice()
    .reverse()
    .map(
      (entry) =>
        `<div class="log-line">[${entry.t}] <span class="dir-${entry.dir}">${entry.dir}</span> len=${entry.preview.len}${
          entry.preview.truncated ? "+" : ""
        }  hex: ${entry.preview.hex}  text: ${escapeHtml(entry.preview.text)}</div>`
    )
    .join("");
}

function buildRow(c) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td class="cell-status"></td>
    <td>${c.ip}:${c.port}</td>
    <td>${c.connect_time}</td>
    <td class="cell-idle"></td>
    <td class="cell-bytes"></td>
    <td class="cell-count"></td>
    <td>
      <div class="send-row">
        <input type="text" class="send-text" placeholder="테스트 문자열">
        <button type="button" class="send-once-btn">전송</button>
        <input type="number" class="send-interval" min="0.2" step="0.1" value="1" title="반복 주기(초)">
        <button type="button" class="send-repeat-btn">반복 시작</button>
      </div>
    </td>
    <td><button type="button" class="toggle-log-btn">보기</button></td>
  `;

  const logTr = document.createElement("tr");
  const logTd = document.createElement("td");
  logTd.colSpan = 8;
  const logDiv = document.createElement("div");
  logDiv.className = "log-detail";
  logTd.appendChild(logDiv);
  logTr.appendChild(logTd);

  const input = tr.querySelector(".send-text");
  const sendBtn = tr.querySelector(".send-once-btn");
  const intervalInput = tr.querySelector(".send-interval");
  const repeatBtn = tr.querySelector(".send-repeat-btn");
  const toggleBtn = tr.querySelector(".toggle-log-btn");

  let repeatTimer = null;

  const doSend = (clearAfter) => {
    const text = input.value;
    if (!text) return;
    fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: c.client_id, text }),
    }).then(() => {
      if (clearAfter) input.value = "";
    });
  };

  const stopRepeat = () => {
    if (repeatTimer) {
      clearInterval(repeatTimer);
      repeatTimer = null;
    }
    repeatBtn.textContent = "반복 시작";
    repeatBtn.classList.remove("active");
  };

  sendBtn.addEventListener("click", () => doSend(true));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSend(true);
  });

  repeatBtn.addEventListener("click", () => {
    if (repeatTimer) {
      stopRepeat();
      return;
    }
    if (!input.value) return;
    const sec = Math.max(0.2, parseFloat(intervalInput.value) || 1);
    doSend(false);
    repeatTimer = setInterval(() => doSend(false), sec * 1000);
    repeatBtn.textContent = "반복 중지";
    repeatBtn.classList.add("active");
  });

  toggleBtn.addEventListener("click", () => {
    logDiv.classList.toggle("open");
    toggleBtn.textContent = logDiv.classList.contains("open") ? "숨기기" : "보기";
  });

  return {
    tr,
    logTr,
    logDiv,
    input,
    sendBtn,
    intervalInput,
    repeatBtn,
    toggleBtn,
    stopRepeat,
    isRepeating: () => repeatTimer !== null,
    statusCell: tr.querySelector(".cell-status"),
    idleCell: tr.querySelector(".cell-idle"),
    bytesCell: tr.querySelector(".cell-bytes"),
    countCell: tr.querySelector(".cell-count"),
  };
}

function updateRow(row, c) {
  row.tr.className = c.status === "disconnected" ? "disconnected" : "";
  row.statusCell.innerHTML = `<span class="badge ${STATUS_BADGE[c.status]}">${STATUS_LABEL[c.status]}</span>`;
  row.idleCell.textContent = idleText(c);
  row.bytesCell.textContent = `${fmtBytes(c.bytes_recv)} / ${fmtBytes(c.bytes_sent)}`;
  row.countCell.textContent = c.recv_count;
  const disabled = c.status === "disconnected";
  if (disabled && row.isRepeating()) row.stopRepeat();
  row.input.disabled = disabled;
  row.sendBtn.disabled = disabled;
  row.intervalInput.disabled = disabled;
  row.repeatBtn.disabled = disabled;
  row.logDiv.innerHTML = renderLog(c);
}

function upsert(c) {
  clients.set(c.client_id, c);
  let row = rowsMap.get(c.client_id);
  if (!row) {
    row = buildRow(c);
    rowsMap.set(c.client_id, row);
    tbody.insertBefore(row.logTr, tbody.firstChild);
    tbody.insertBefore(row.tr, row.logTr);
  }
  updateRow(row, c);
  clientCountEl.textContent = clients.size;
}

function removeRow(clientId) {
  const row = rowsMap.get(clientId);
  if (!row) return;
  row.stopRepeat();
  row.tr.remove();
  row.logTr.remove();
  rowsMap.delete(clientId);
  clients.delete(clientId);
  clientCountEl.textContent = clients.size;
}

function pushEvent(kind, c, message) {
  const line = document.createElement("div");
  line.className = `event-line ev-${kind}`;
  const t = new Date().toLocaleTimeString();
  line.innerHTML = `<span class="t">${t}</span>[${c.ip}:${c.port}] ${message}`;
  eventLog.appendChild(line);
  while (eventLog.children.length > 300) eventLog.removeChild(eventLog.firstChild);
}

document.getElementById("clear-disconnected").addEventListener("click", () => {
  for (const [id, c] of clients) {
    if (c.status === "disconnected") removeRow(id);
  }
});

document.getElementById("clear-events").addEventListener("click", () => {
  eventLog.innerHTML = "";
});

// Tick only the idle-seconds cell every second, no DOM rebuild.
setInterval(() => {
  for (const [id, row] of rowsMap) {
    const c = clients.get(id);
    if (c) row.idleCell.textContent = idleText(c);
  }
}, 1000);
