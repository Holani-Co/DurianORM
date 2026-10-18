<script setup>
// Replace the price list from a link-shared Google Sheet or an uploaded file.
// Both paths go through the same preview (what's new / removed / repriced, plus
// row warnings) — nothing goes live until Publish. A linked sheet can re-sync
// daily; uploading a file pauses that so the sheet can't overwrite the upload.
import { ref, computed, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  sync: { type: Object, default: () => ({}) },
});
const emit = defineEmits(['published', 'changed']);

const MAX_FILE_BYTES = 15 * 1024 * 1024;

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;
const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/product_catalog_config`;

const sheetUrl = ref(props.sync.sheet_url || '');
watch(
  () => props.sync.sheet_url,
  v => {
    sheetUrl.value = v || '';
  }
);

const busy = ref('');
const preview = ref(null);
const pending = ref(null);
const pricePeriod = ref('');
const note = ref('');
const autoSyncOnPublish = ref(true);
const fileInput = ref(null);

const linked = computed(() => props.sync.sheet_url || '');
const lastSync = computed(() => props.sync.last || null);

const rupees = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});
const price = v => (typeof v === 'number' && v > 0 ? rupees.format(v) : '—');

const formatWhen = iso => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

const errorText = (e, fallbackKey) =>
  e?.response?.data?.detail?.message ||
  e?.response?.data?.error ||
  t(fallbackKey);

const lastSyncResult = computed(() => {
  const s = lastSync.value;
  if (!s) return '';
  if (!s.ok) return t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_FAILED', { error: s.error });
  return s.changed
    ? t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_CHANGED')
    : t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_UNCHANGED');
});

async function runPreview(body) {
  busy.value = 'preview';
  preview.value = null;
  pending.value = null;
  try {
    const { data } = await axios.post(`${base()}/preview`, body);
    preview.value = data;
    pending.value = body;
    autoSyncOnPublish.value = true;
  } catch (e) {
    useAlert(errorText(e, 'PRODUCT_CATALOG_CONFIG.UPDATE.READ_FAILED'));
  } finally {
    busy.value = '';
  }
}

const previewSheet = () => runPreview({ sheet_url: sheetUrl.value.trim() });

const readAsBase64 = file =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });

async function onFile(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  if (file.size > MAX_FILE_BYTES) {
    useAlert(t('PRODUCT_CATALOG_CONFIG.UPDATE.FILE_TOO_LARGE'));
    return;
  }
  try {
    const fileB64 = await readAsBase64(file);
    await runPreview({ file_b64: fileB64, filename: file.name });
  } catch (e) {
    useAlert(t('PRODUCT_CATALOG_CONFIG.UPDATE.READ_FAILED'));
  }
}

function cancelPreview() {
  preview.value = null;
  pending.value = null;
  pricePeriod.value = '';
  note.value = '';
}

async function publish() {
  if (!pending.value || busy.value) return;
  busy.value = 'publish';
  try {
    const { data } = await axios.post(`${base()}/publish`, {
      ...pending.value,
      price_period: pricePeriod.value.trim(),
      note: note.value.trim(),
      auto_sync: autoSyncOnPublish.value,
    });
    useAlert(
      data.auto_sync_paused
        ? t('PRODUCT_CATALOG_CONFIG.PREVIEW.PUBLISHED_PAUSED')
        : t('PRODUCT_CATALOG_CONFIG.PREVIEW.PUBLISHED')
    );
    cancelPreview();
    emit('published');
  } catch (e) {
    useAlert(errorText(e, 'PRODUCT_CATALOG_CONFIG.PREVIEW.FAILED'));
  } finally {
    busy.value = '';
  }
}

async function syncNow() {
  if (busy.value) return;
  busy.value = 'sync';
  try {
    const { data } = await axios.post(`${base()}/sync`, {});
    if (!data.ok) {
      useAlert(
        t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_FAILED', { error: data.error })
      );
    } else {
      useAlert(
        data.changed
          ? t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_DONE_CHANGED')
          : t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_DONE_UNCHANGED')
      );
    }
    emit('changed');
  } catch (e) {
    useAlert(errorText(e, 'PRODUCT_CATALOG_CONFIG.UPDATE.SETTINGS_FAILED'));
  } finally {
    busy.value = '';
  }
}

async function saveSyncSettings(body) {
  if (busy.value) return;
  busy.value = 'settings';
  try {
    await axios.post(`${base()}/sync_settings`, body);
    useAlert(t('PRODUCT_CATALOG_CONFIG.UPDATE.SETTINGS_SAVED'));
    emit('changed');
  } catch (e) {
    useAlert(errorText(e, 'PRODUCT_CATALOG_CONFIG.UPDATE.SETTINGS_FAILED'));
  } finally {
    busy.value = '';
  }
}

const summary = computed(() => preview.value?.summary || {});
const fromLabel = computed(() =>
  preview.value?.source === 'sheet'
    ? t('PRODUCT_CATALOG_CONFIG.PREVIEW.FROM_SHEET')
    : t('PRODUCT_CATALOG_CONFIG.PREVIEW.FROM_FILE', {
        name: preview.value?.source_name,
      })
);
const formatLabel = computed(() =>
  preview.value?.format === 'workbook'
    ? t('PRODUCT_CATALOG_CONFIG.PREVIEW.FORMAT_WORKBOOK')
    : t('PRODUCT_CATALOG_CONFIG.PREVIEW.FORMAT_TEMPLATE')
);
</script>

<template>
  <div class="flex flex-col max-w-3xl gap-6">
    <!-- Preview (shown instead of the inputs until published or cancelled) -->
    <section
      v-if="preview"
      class="p-4 border rounded-xl border-n-brand bg-n-alpha-1"
    >
      <h2 class="text-base font-medium text-n-slate-12">
        {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.TITLE') }}
      </h2>
      <p class="mt-1 text-xs text-n-slate-11">
        {{ fromLabel }} · {{ formatLabel }}
      </p>

      <p class="mt-3 text-sm font-medium text-n-slate-12">
        {{
          t('PRODUCT_CATALOG_CONFIG.PREVIEW.COUNTS', {
            count: summary.count,
            previous: summary.previous_count,
          })
        }}
      </p>
      <div class="flex flex-wrap gap-2 mt-2 text-xs">
        <span class="px-2 py-0.5 rounded-full bg-n-teal-3 text-n-teal-11">
          {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.ADDED', { n: summary.added }) }}
        </span>
        <span class="px-2 py-0.5 rounded-full bg-n-ruby-3 text-n-ruby-11">
          {{
            t('PRODUCT_CATALOG_CONFIG.PREVIEW.REMOVED', { n: summary.removed })
          }}
        </span>
        <span class="px-2 py-0.5 rounded-full bg-n-amber-3 text-n-amber-11">
          {{
            t('PRODUCT_CATALOG_CONFIG.PREVIEW.PRICE_CHANGED', {
              n: summary.price_changed,
            })
          }}
        </span>
        <span class="px-2 py-0.5 rounded-full bg-n-alpha-2 text-n-slate-11">
          {{
            t('PRODUCT_CATALOG_CONFIG.PREVIEW.DETAILS_CHANGED', {
              n: summary.details_changed,
            })
          }}
        </span>
        <span class="px-2 py-0.5 rounded-full bg-n-alpha-2 text-n-slate-11">
          {{
            t('PRODUCT_CATALOG_CONFIG.PREVIEW.UNCHANGED', {
              n: summary.unchanged,
            })
          }}
        </span>
      </div>

      <p
        v-if="summary.removed"
        class="p-2 mt-3 text-xs rounded-lg bg-n-ruby-2 text-n-ruby-11"
      >
        {{
          t('PRODUCT_CATALOG_CONFIG.PREVIEW.REMOVED_WARNING', {
            n: summary.removed,
          })
        }}
      </p>

      <div
        v-if="preview.warnings?.length"
        class="p-2 mt-3 text-xs rounded-lg bg-n-amber-2 text-n-amber-11"
      >
        <div class="mb-1 font-medium">
          {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.WARNINGS') }}
        </div>
        <ul class="list-disc list-inside">
          <li v-for="(w, i) in preview.warnings" :key="i">{{ w }}</li>
        </ul>
      </div>

      <div class="grid gap-3 mt-3 text-xs sm:grid-cols-3">
        <div v-if="summary.changed_sample?.length">
          <div class="mb-1 font-medium text-n-slate-12">
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_CHANGED') }}
          </div>
          <div
            v-for="c in summary.changed_sample"
            :key="c.sku"
            class="text-n-slate-11"
          >
            <span class="font-mono">{{ c.sku }}</span>
            {{ price(c.old) }} → {{ price(c.new) }}
          </div>
          <div
            v-if="summary.price_changed > summary.changed_sample.length"
            class="text-n-slate-10"
          >
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_MORE') }}
          </div>
        </div>
        <div v-if="summary.added_sample?.length">
          <div class="mb-1 font-medium text-n-slate-12">
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_ADDED') }}
          </div>
          <div
            v-for="a in summary.added_sample"
            :key="a.sku"
            class="text-n-slate-11"
          >
            <span class="font-mono">{{ a.sku }}</span>
            {{ price(a.sale_price) }}
          </div>
          <div
            v-if="summary.added > summary.added_sample.length"
            class="text-n-slate-10"
          >
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_MORE') }}
          </div>
        </div>
        <div v-if="summary.removed_sample?.length">
          <div class="mb-1 font-medium text-n-slate-12">
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_REMOVED') }}
          </div>
          <div
            v-for="r in summary.removed_sample"
            :key="r.sku"
            class="text-n-slate-11"
          >
            <span class="font-mono">{{ r.sku }}</span>
            {{ price(r.sale_price) }}
          </div>
          <div
            v-if="summary.removed > summary.removed_sample.length"
            class="text-n-slate-10"
          >
            {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.SAMPLE_MORE') }}
          </div>
        </div>
      </div>

      <div class="grid gap-3 mt-4 sm:grid-cols-2">
        <label class="text-xs text-n-slate-11">
          {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.PRICE_PERIOD') }}
          <input
            v-model="pricePeriod"
            type="text"
            :placeholder="
              t('PRODUCT_CATALOG_CONFIG.PREVIEW.PRICE_PERIOD_PLACEHOLDER')
            "
            class="w-full px-3 py-2 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
        </label>
        <label class="text-xs text-n-slate-11">
          {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.NOTE') }}
          <input
            v-model="note"
            type="text"
            class="w-full px-3 py-2 mt-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
        </label>
      </div>

      <label
        v-if="preview.source === 'sheet'"
        class="flex items-center gap-2 mt-3 text-sm text-n-slate-12"
      >
        <input v-model="autoSyncOnPublish" type="checkbox" />
        {{
          t('PRODUCT_CATALOG_CONFIG.UPDATE.AUTO_SYNC', { time: sync.time })
        }}
      </label>

      <div class="flex justify-end gap-2 mt-4">
        <button
          type="button"
          class="px-3 py-2 text-sm rounded-lg text-n-slate-11 hover:text-n-slate-12"
          :disabled="busy === 'publish'"
          @click="cancelPreview"
        >
          {{ t('PRODUCT_CATALOG_CONFIG.PREVIEW.CANCEL') }}
        </button>
        <button
          type="button"
          class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy === 'publish'"
          @click="publish"
        >
          {{
            busy === 'publish'
              ? t('PRODUCT_CATALOG_CONFIG.PREVIEW.PUBLISHING')
              : t('PRODUCT_CATALOG_CONFIG.PREVIEW.PUBLISH')
          }}
        </button>
      </div>
    </section>

    <template v-else>
      <!-- Google Sheet -->
      <section class="p-4 border rounded-xl border-n-weak">
        <h2 class="text-base font-medium text-n-slate-12">
          {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.SHEET_TITLE') }}
        </h2>
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.SHEET_HELP') }}
        </p>

        <div v-if="linked" class="p-3 mt-3 rounded-lg bg-n-alpha-1">
          <div class="text-xs text-n-slate-11">
            {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.LINKED') }}
          </div>
          <a
            :href="linked"
            target="_blank"
            rel="noopener noreferrer"
            class="text-sm break-all text-n-brand"
            >{{ linked }}</a
          >
          <label class="flex items-center gap-2 mt-2 text-sm text-n-slate-12">
            <input
              type="checkbox"
              :checked="sync.auto_sync"
              :disabled="busy !== ''"
              @change="saveSyncSettings({ auto_sync: $event.target.checked })"
            />
            {{
              t('PRODUCT_CATALOG_CONFIG.UPDATE.AUTO_SYNC', { time: sync.time })
            }}
          </label>
          <p v-if="!sync.auto_sync" class="mt-1 text-xs text-n-amber-11">
            {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.AUTO_SYNC_OFF_HINT') }}
          </p>
          <p v-if="lastSync" class="mt-1 text-xs text-n-slate-11">
            {{
              t('PRODUCT_CATALOG_CONFIG.UPDATE.LAST_SYNC', {
                when: formatWhen(lastSync.at),
                result: lastSyncResult,
              })
            }}
          </p>
          <div class="flex gap-2 mt-3">
            <button
              type="button"
              class="px-3 py-1.5 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
              :disabled="busy !== ''"
              @click="syncNow"
            >
              {{
                busy === 'sync'
                  ? t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNCING')
                  : t('PRODUCT_CATALOG_CONFIG.UPDATE.SYNC_NOW')
              }}
            </button>
            <button
              type="button"
              class="px-3 py-1.5 text-sm rounded-lg text-n-ruby-11 hover:bg-n-alpha-2 disabled:opacity-60"
              :disabled="busy !== ''"
              @click="saveSyncSettings({ unlink: true })"
            >
              {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.UNLINK') }}
            </button>
          </div>
        </div>

        <div class="flex flex-wrap gap-2 mt-3">
          <input
            v-model="sheetUrl"
            type="url"
            :placeholder="t('PRODUCT_CATALOG_CONFIG.UPDATE.SHEET_PLACEHOLDER')"
            class="flex-1 min-w-[16rem] px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <button
            type="button"
            class="px-3 py-2 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1 disabled:opacity-60"
            :disabled="!sheetUrl.trim() || busy !== ''"
            @click="previewSheet"
          >
            {{
              busy === 'preview'
                ? t('PRODUCT_CATALOG_CONFIG.UPDATE.READING')
                : t('PRODUCT_CATALOG_CONFIG.UPDATE.PREVIEW_SHEET')
            }}
          </button>
        </div>
      </section>

      <!-- File upload -->
      <section class="p-4 border rounded-xl border-n-weak">
        <h2 class="text-base font-medium text-n-slate-12">
          {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.FILE_TITLE') }}
        </h2>
        <p class="mt-1 text-xs text-n-slate-11">
          {{ t('PRODUCT_CATALOG_CONFIG.UPDATE.FILE_HELP') }}
        </p>
        <input
          ref="fileInput"
          type="file"
          accept=".xlsx,.csv"
          class="hidden"
          @change="onFile"
        />
        <button
          type="button"
          class="px-3 py-2 mt-3 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1 disabled:opacity-60"
          :disabled="busy !== ''"
          @click="fileInput.click()"
        >
          {{
            busy === 'preview'
              ? t('PRODUCT_CATALOG_CONFIG.UPDATE.READING')
              : t('PRODUCT_CATALOG_CONFIG.UPDATE.CHOOSE_FILE')
          }}
        </button>
      </section>
    </template>
  </div>
</template>
