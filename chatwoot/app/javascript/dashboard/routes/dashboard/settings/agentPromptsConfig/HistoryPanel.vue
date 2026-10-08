<script setup>
// Version history + one-click rollback for the agent-prompts config (restores
// are audited). Backed by the bridge's /versions and /rollback via the proxy.
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const emit = defineEmits(['restored']);

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const loading = ref(true);
const error = ref(false);
const versions = ref([]);
const audit = ref([]);
const busy = ref(false);
const confirmingId = ref(null);
const expandedId = ref(null);
const expandedDoc = ref({});
const LABELS = {
  furniture: t('AGENT_PROMPTS_CONFIG.VERTICALS.FURNITURE'),
  doors: t('AGENT_PROMPTS_CONFIG.VERTICALS.DOORS'),
  fhc: t('AGENT_PROMPTS_CONFIG.VERTICALS.FHC'),
};

const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/agent_prompts_config`;

const formatWhen = iso => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

async function load() {
  loading.value = true;
  error.value = false;
  try {
    const { data } = await axios.get(`${base()}/versions`);
    versions.value = data.versions || [];
    audit.value = data.audit || [];
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

async function toggleView(id) {
  if (expandedId.value === id) {
    expandedId.value = null;
    expandedDoc.value = {};
    return;
  }
  expandedId.value = id;
  expandedDoc.value = {};
  try {
    const { data } = await axios.get(`${base()}/version`, {
      params: { version_id: id },
    });
    expandedDoc.value = data.doc || {};
  } catch (e) {
    expandedDoc.value = {};
  }
}

async function restore(id) {
  if (busy.value) return;
  busy.value = true;
  try {
    await axios.post(`${base()}/rollback`, { version_id: id });
    useAlert(t('AGENT_PROMPTS_CONFIG.HISTORY.RESTORED'));
    confirmingId.value = null;
    await load();
    emit('restored');
  } catch (e) {
    useAlert(
      e?.response?.data?.error ||
        t('AGENT_PROMPTS_CONFIG.HISTORY.RESTORE_FAILED')
    );
  } finally {
    busy.value = false;
  }
}

const auditVerb = action =>
  action === 'rollback'
    ? t('AGENT_PROMPTS_CONFIG.HISTORY.AUDIT_ROLLBACK')
    : t('AGENT_PROMPTS_CONFIG.HISTORY.AUDIT_PUBLISH');
</script>

<template>
  <div class="max-w-3xl">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('AGENT_PROMPTS_CONFIG.HISTORY.SUBTITLE') }}
    </p>

    <div v-if="loading" class="py-8 text-sm text-n-slate-11">
      {{ t('AGENT_PROMPTS_CONFIG.HISTORY.LOADING') }}
    </div>

    <div
      v-else-if="error"
      class="p-3 text-sm border rounded-lg border-n-weak bg-n-ruby-2 text-n-ruby-11"
    >
      {{ t('AGENT_PROMPTS_CONFIG.HISTORY.ERROR') }}
    </div>

    <div v-else-if="!versions.length" class="py-8 text-sm text-n-slate-11">
      {{ t('AGENT_PROMPTS_CONFIG.HISTORY.EMPTY') }}
    </div>

    <div v-else class="flex flex-col gap-2">
      <div
        v-for="v in versions"
        :key="v.id"
        class="border rounded-xl border-n-weak"
        :class="v.active ? 'border-n-brand' : ''"
      >
        <div class="flex flex-wrap items-center gap-2 px-4 py-3">
          <span class="font-medium text-n-slate-12">
            {{ t('AGENT_PROMPTS_CONFIG.HISTORY.VERSION') }} {{ v.id }}
          </span>
          <span
            v-if="v.active"
            class="px-2 py-0.5 text-xs font-medium rounded-full bg-n-teal-3 text-n-teal-11"
          >
            {{ t('AGENT_PROMPTS_CONFIG.HISTORY.ACTIVE') }}
          </span>
          <span v-if="v.note" class="text-sm text-n-slate-11">{{
            v.note
          }}</span>
          <span class="text-xs text-n-slate-10">
            {{ t('AGENT_PROMPTS_CONFIG.HISTORY.BY') }} {{ v.created_by || '—' }}
          </span>
          <span class="text-xs text-n-slate-10">{{
            formatWhen(v.created_at)
          }}</span>

          <span class="flex items-center gap-2 ml-auto">
            <button
              type="button"
              class="px-2 py-1 text-xs rounded-lg text-n-slate-11 hover:text-n-slate-12"
              @click="toggleView(v.id)"
            >
              {{
                expandedId === v.id
                  ? t('AGENT_PROMPTS_CONFIG.HISTORY.HIDE')
                  : t('AGENT_PROMPTS_CONFIG.HISTORY.VIEW')
              }}
            </button>

            <template v-if="!v.active">
              <template v-if="confirmingId === v.id">
                <button
                  type="button"
                  class="px-3 py-1 text-xs font-medium text-white rounded-lg bg-n-ruby-11 hover:opacity-90 disabled:opacity-60"
                  :disabled="busy"
                  @click="restore(v.id)"
                >
                  {{ t('AGENT_PROMPTS_CONFIG.HISTORY.CONFIRM_RESTORE') }}
                </button>
                <button
                  type="button"
                  class="px-2 py-1 text-xs rounded-lg text-n-slate-11 hover:text-n-slate-12"
                  :disabled="busy"
                  @click="confirmingId = null"
                >
                  {{ t('AGENT_PROMPTS_CONFIG.HISTORY.CANCEL') }}
                </button>
              </template>
              <button
                v-else
                type="button"
                class="px-3 py-1 text-xs font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1"
                @click="confirmingId = v.id"
              >
                {{ t('AGENT_PROMPTS_CONFIG.HISTORY.RESTORE') }}
              </button>
            </template>
          </span>
        </div>

        <div
          v-if="confirmingId === v.id"
          class="px-4 pb-3 text-xs text-n-amber-11"
        >
          {{ t('AGENT_PROMPTS_CONFIG.HISTORY.CONFIRM_HINT') }}
        </div>

        <div v-if="expandedId === v.id" class="px-4 pb-3">
          <div
            v-if="Object.keys(expandedDoc).length"
            class="flex flex-col gap-2"
          >
            <div
              v-for="(text, vert) in expandedDoc"
              :key="vert"
              class="p-3 text-xs rounded-lg bg-n-alpha-1 text-n-slate-12"
            >
              <div class="mb-1 font-medium">{{ LABELS[vert] || vert }}</div>
              <div class="whitespace-pre-wrap">{{ text }}</div>
            </div>
          </div>
          <div v-else class="text-xs text-n-slate-10">
            {{ t('AGENT_PROMPTS_CONFIG.HISTORY.NO_CHANGES') }}
          </div>
        </div>
      </div>
    </div>

    <div v-if="audit.length" class="mt-8">
      <div class="mb-2 text-sm font-medium text-n-slate-12">
        {{ t('AGENT_PROMPTS_CONFIG.HISTORY.AUDIT_TITLE') }}
      </div>
      <ul class="flex flex-col gap-1">
        <li
          v-for="a in audit"
          :key="a.id"
          class="flex flex-wrap gap-2 text-xs text-n-slate-11"
        >
          <span class="text-n-slate-12">{{ a.actor || '—' }}</span>
          <span>{{ auditVerb(a.action) }}</span>
          <span class="text-n-slate-12"
            >{{ t('AGENT_PROMPTS_CONFIG.HISTORY.VERSION') }}
            {{ a.version_id }}</span
          >
          <span class="text-n-slate-10">{{ formatWhen(a.created_at) }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>
