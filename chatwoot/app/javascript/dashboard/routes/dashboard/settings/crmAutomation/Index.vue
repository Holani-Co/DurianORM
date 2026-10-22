<script setup>
// CRM WhatsApp Automation (via the admin Rails proxy): rules that send a
// WhatsApp template when a Zoho deal reaches a stage. Edits are made on a draft
// and go live with Publish (versioned, restorable from History).
import { ref, computed, onMounted, defineAsyncComponent } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const RulesEditor = defineAsyncComponent(() => import('./RulesEditor.vue'));
const TeamEditor = defineAsyncComponent(() => import('./TeamEditor.vue'));
const ActivityPanel = defineAsyncComponent(() => import('./ActivityPanel.vue'));
const TestPanel = defineAsyncComponent(() => import('./TestPanel.vue'));
const HistoryPanel = defineAsyncComponent(() => import('./HistoryPanel.vue'));

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;
const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/crm_automation_config`;

const loading = ref(true);
const error = ref(false);
const data = ref(null);
const draft = ref(null);
const saved = ref('');
const activeTab = ref('rules');
const note = ref('');
const busy = ref(false);
const problems = ref({ errors: [], warnings: [] });

const clone = obj => JSON.parse(JSON.stringify(obj));

async function load() {
  loading.value = !data.value;
  error.value = false;
  try {
    const { data: res } = await axios.get(base());
    data.value = res;
    draft.value = clone(res.doc);
    saved.value = JSON.stringify(res.doc);
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const dirty = computed(
  () => draft.value && JSON.stringify(draft.value) !== saved.value
);
const version = computed(() => data.value?.active_version || null);
const webhookUrl = computed(
  () => `${window.location.origin}/webhooks/crm_stage?key=YOUR_SECRET`
);

const formatWhen = iso => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

async function publish() {
  if (busy.value) return;
  busy.value = true;
  try {
    const { data: v } = await axios.post(`${base()}/validate`, {
      doc: draft.value,
    });
    problems.value = { errors: v.errors || [], warnings: v.warnings || [] };
    if (!v.ok) return;
    await axios.post(`${base()}/publish`, {
      doc: draft.value,
      note: note.value.trim(),
    });
    useAlert(t('CRM_AUTOMATION_CONFIG.SAVE.PUBLISHED'));
    note.value = '';
    await load();
  } catch (e) {
    const detail = e?.response?.data?.detail;
    if (detail?.errors) {
      problems.value = { errors: detail.errors, warnings: detail.warnings || [] };
    } else {
      useAlert(t('CRM_AUTOMATION_CONFIG.SAVE.FAILED'));
    }
  } finally {
    busy.value = false;
  }
}

const tabs = computed(() =>
  ['rules', 'team', 'activity', 'test', 'history'].map(key => ({
    key,
    label: t(`CRM_AUTOMATION_CONFIG.TABS.${key.toUpperCase()}`),
  }))
);
</script>

<template>
  <div class="w-full">
    <div class="mb-6">
      <h1 class="text-xl font-semibold text-n-slate-12">
        {{ t('CRM_AUTOMATION_CONFIG.HEADER') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('CRM_AUTOMATION_CONFIG.DESCRIPTION') }}
      </p>
    </div>

    <div v-if="loading" class="py-10 text-sm text-n-slate-11">
      {{ t('CRM_AUTOMATION_CONFIG.LOADING') }}
    </div>
    <div
      v-else-if="error"
      class="p-4 text-sm border rounded-lg border-n-weak bg-n-alpha-2 text-n-ruby-11"
    >
      {{ t('CRM_AUTOMATION_CONFIG.BRIDGE_UNAVAILABLE') }}
    </div>

    <div v-else class="flex flex-col gap-4">
      <div class="flex flex-wrap items-center gap-3">
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full"
          :class="
            data.doc.enabled
              ? 'bg-n-teal-3 text-n-teal-11'
              : 'bg-n-amber-3 text-n-amber-11'
          "
        >
          {{
            data.doc.enabled
              ? t('CRM_AUTOMATION_CONFIG.ON')
              : t('CRM_AUTOMATION_CONFIG.OFF')
          }}
        </span>
        <span class="text-xs text-n-slate-11">
          {{
            version
              ? t('CRM_AUTOMATION_CONFIG.VERSION_INFO', {
                  version: version.id,
                  actor: version.created_by || '—',
                  when: formatWhen(version.created_at),
                })
              : t('CRM_AUTOMATION_CONFIG.NO_VERSION')
          }}
        </span>
      </div>

      <details class="p-4 border rounded-xl border-n-weak">
        <summary class="text-sm font-medium cursor-pointer text-n-slate-12">
          {{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.TITLE') }}
        </summary>
        <p
          v-if="!data.webhook_ready"
          class="p-2 mt-3 text-xs rounded-lg bg-n-amber-2 text-n-amber-11"
        >
          {{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.NOT_READY') }}
        </p>
        <ol class="mt-3 text-xs list-decimal list-inside text-n-slate-11">
          <li>{{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.STEP_1') }}</li>
          <li>{{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.STEP_2') }}</li>
          <li>
            {{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.STEP_3') }}
            <code class="block p-2 mt-1 rounded bg-n-alpha-2 text-n-slate-12">
              {{ webhookUrl }}
            </code>
          </li>
          <li>{{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.STEP_4') }}</li>
          <li>{{ t('CRM_AUTOMATION_CONFIG.WEBHOOK.STEP_5') }}</li>
        </ol>
      </details>

      <div class="flex gap-1 border-b border-n-weak">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="px-3 py-2 -mb-px text-sm border-b-2"
          :class="
            activeTab === tab.key
              ? 'border-n-brand text-n-slate-12 font-medium'
              : 'border-transparent text-n-slate-11'
          "
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <template v-if="activeTab === 'rules' || activeTab === 'team'">
        <div class="flex flex-wrap items-center gap-4">
          <label class="flex items-center gap-2 text-sm text-n-slate-12">
            <input v-model="draft.enabled" type="checkbox" />
            {{ t('CRM_AUTOMATION_CONFIG.MASTER_SWITCH') }}
          </label>
          <label class="flex items-center gap-2 text-xs text-n-slate-11">
            {{ t('CRM_AUTOMATION_CONFIG.INBOX') }}
            <input
              v-model.number="draft.inbox_id"
              type="number"
              class="w-20 px-2 py-1 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
            />
          </label>
        </div>
        <RulesEditor
          v-if="activeTab === 'rules'"
          v-model:rules="draft.rules"
          :templates="data.templates"
          :sources="data.sources"
          :ops="data.ops"
        />
        <TeamEditor v-else v-model:team="draft.team" />

        <div
          v-if="problems.errors.length || problems.warnings.length"
          class="flex flex-col gap-2"
        >
          <div
            v-if="problems.errors.length"
            class="p-3 text-xs rounded-lg bg-n-ruby-2 text-n-ruby-11"
          >
            <div class="mb-1 font-medium">
              {{ t('CRM_AUTOMATION_CONFIG.SAVE.ERRORS') }}
            </div>
            <ul class="list-disc list-inside">
              <li v-for="(m, i) in problems.errors" :key="i">{{ m }}</li>
            </ul>
          </div>
          <div
            v-if="problems.warnings.length"
            class="p-3 text-xs rounded-lg bg-n-amber-2 text-n-amber-11"
          >
            <div class="mb-1 font-medium">
              {{ t('CRM_AUTOMATION_CONFIG.SAVE.WARNINGS') }}
            </div>
            <ul class="list-disc list-inside">
              <li v-for="(m, i) in problems.warnings" :key="i">{{ m }}</li>
            </ul>
          </div>
        </div>

        <div
          class="sticky bottom-0 flex flex-wrap items-center gap-3 py-3 bg-n-background"
        >
          <span v-if="dirty" class="text-xs text-n-amber-11">
            {{ t('CRM_AUTOMATION_CONFIG.SAVE.UNSAVED') }}
          </span>
          <input
            v-model="note"
            type="text"
            :placeholder="t('CRM_AUTOMATION_CONFIG.SAVE.NOTE')"
            class="flex-1 min-w-[12rem] px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
          />
          <button
            type="button"
            class="px-4 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
            :disabled="busy || !dirty"
            @click="publish"
          >
            {{
              busy
                ? t('CRM_AUTOMATION_CONFIG.SAVE.PUBLISHING')
                : t('CRM_AUTOMATION_CONFIG.SAVE.PUBLISH')
            }}
          </button>
        </div>
      </template>

      <ActivityPanel v-else-if="activeTab === 'activity'" :runs="data.runs" />
      <TestPanel v-else-if="activeTab === 'test'" :rules="data.doc.rules" />
      <HistoryPanel v-else @restored="load" />
    </div>
  </div>
</template>
