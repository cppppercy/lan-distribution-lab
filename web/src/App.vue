<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { ApiFailure, listFiles, pair, revokeSession, startDownload, uploadFile } from "./api";
import type { FileRecord, Scope } from "./types";

type BrowserScope = Scope | "unknown";

const pairingCode = ref("");
const scope = ref<BrowserScope | null>(null);
const files = ref<FileRecord[]>([]);
const selectedFile = ref<File | null>(null);
const busy = ref(false);
const loadingFiles = ref(false);
const uploadPercent = ref<number | null>(null);
const notice = ref<string | null>(null);
const error = ref<string | null>(null);

const paired = computed(() => scope.value !== null);
const canUpload = computed(() => scope.value !== "read");

onMounted(loadFiles);

async function loadFiles(): Promise<void> {
  loadingFiles.value = true;
  error.value = null;
  try {
    const response = await listFiles();
    files.value = response.items;
    // Existing HttpOnly cookies cannot be inspected. A successful protected call
    // proves a usable browser session without exposing the token to JavaScript.
    scope.value = scope.value ?? "unknown";
  } catch (caught) {
    if (isFailure(caught, 401)) {
      scope.value = null;
      files.value = [];
      return;
    }
    error.value = messageOf(caught);
  } finally {
    loadingFiles.value = false;
  }
}

async function submitPairing(): Promise<void> {
  const code = pairingCode.value.trim();
  if (!/^\d{6}$/.test(code)) {
    error.value = "请输入六位数字配对码。";
    return;
  }
  busy.value = true;
  error.value = null;
  notice.value = null;
  try {
    const response = await pair(code);
    scope.value = response.session.scope;
    pairingCode.value = "";
    await loadFiles();
    notice.value = "配对成功。";
  } catch (caught) {
    error.value = messageOf(caught);
  } finally {
    busy.value = false;
  }
}

function chooseFile(event: Event): void {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  notice.value = null;
  error.value = null;
}

async function submitUpload(): Promise<void> {
  if (!selectedFile.value) return;
  busy.value = true;
  error.value = null;
  notice.value = null;
  uploadPercent.value = 0;
  try {
    const result = await uploadFile(selectedFile.value, (loaded, total) => {
      uploadPercent.value = total > 0 ? Math.round((loaded / total) * 100) : 100;
    });
    files.value = [result.file, ...files.value.filter((item) => item.file_id !== result.file.file_id)];
    notice.value = `已上传：${result.file.display_name}`;
    selectedFile.value = null;
  } catch (caught) {
    error.value = messageOf(caught);
  } finally {
    busy.value = false;
    uploadPercent.value = null;
  }
}

function download(file: FileRecord): void {
  notice.value = `已发起下载：${file.display_name}。请在浏览器下载列表确认是否保存完成。`;
  startDownload(file);
}

async function logout(): Promise<void> {
  busy.value = true;
  error.value = null;
  try {
    await revokeSession();
  } catch (caught) {
    // A stale/expired cookie is functionally logged out as well.
    if (!isFailure(caught, 401)) error.value = messageOf(caught);
  } finally {
    scope.value = null;
    files.value = [];
    selectedFile.value = null;
    busy.value = false;
  }
}

function formatBytes(value: number): string {
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function messageOf(caught: unknown): string {
  return caught instanceof ApiFailure ? caught.message : "发生未预期的错误，请稍后重试。";
}

function isFailure(caught: unknown, status: number): boolean {
  return caught instanceof ApiFailure && caught.status === status;
}
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">LAN DISTRIBUTION LAB</p>
        <h1>局域网文件传输</h1>
      </div>
      <button v-if="paired" class="quiet-button" :disabled="busy" @click="logout">退出会话</button>
    </header>

    <p v-if="error" class="alert error" role="alert">{{ error }}</p>
    <p v-if="notice" class="alert notice" role="status">{{ notice }}</p>

    <section v-if="!paired" class="card pairing-card" aria-labelledby="pair-heading">
      <p class="eyebrow">安全配对</p>
      <h2 id="pair-heading">输入主机显示的六位配对码</h2>
      <p>配对码仅可使用一次，默认十分钟后过期。请在同一 Wi-Fi 下访问此页面。</p>
      <form @submit.prevent="submitPairing">
        <label for="pairing-code">配对码</label>
        <input id="pairing-code" v-model="pairingCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" :disabled="busy" />
        <button class="primary-button" :disabled="busy">{{ busy ? "正在配对…" : "安全配对" }}</button>
      </form>
      <p class="hint">需要上传权限时，请让主机操作者生成 <code>read_write</code> 配对码。</p>
    </section>

    <template v-else>
      <section class="card upload-card" aria-labelledby="upload-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">上传</p>
            <h2 id="upload-heading">发送文件到此主机</h2>
          </div>
          <span class="scope">{{ scope === "unknown" ? "已授权" : canUpload ? "可上传和下载" : "仅可下载" }}</span>
        </div>
        <p v-if="!canUpload" class="hint">当前会话没有上传权限；请退出后使用 read_write 配对码重新配对。</p>
        <template v-else>
          <label class="file-picker" for="file-input">
            <span>{{ selectedFile ? selectedFile.name : "选择一个文件" }}</span>
            <small>{{ selectedFile ? formatBytes(selectedFile.size) : "支持服务器当前大小限制" }}</small>
          </label>
          <input id="file-input" class="visually-hidden" type="file" :disabled="busy" @change="chooseFile" />
          <button class="primary-button" :disabled="busy || !selectedFile" @click="submitUpload">
            {{ busy && uploadPercent !== null ? `上传中 ${uploadPercent}%` : "开始上传" }}
          </button>
          <progress v-if="uploadPercent !== null" :value="uploadPercent" max="100">{{ uploadPercent }}%</progress>
        </template>
      </section>

      <section class="card files-card" aria-labelledby="files-heading">
        <div class="section-heading">
          <div>
            <p class="eyebrow">文件</p>
            <h2 id="files-heading">可共享文件</h2>
          </div>
          <button class="quiet-button" :disabled="loadingFiles" @click="loadFiles">刷新</button>
        </div>
        <p v-if="loadingFiles" class="hint">正在读取文件列表…</p>
        <p v-else-if="files.length === 0" class="empty">暂无文件。上传后会显示在这里。</p>
        <ul v-else class="file-list">
          <li v-for="file in files" :key="file.file_id" class="file-row">
            <div class="file-copy">
              <strong>{{ file.display_name }}</strong>
              <span>{{ formatBytes(file.size_bytes) }} · {{ formatTime(file.created_at) }}</span>
            </div>
            <button class="secondary-button" @click="download(file)">下载</button>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>
