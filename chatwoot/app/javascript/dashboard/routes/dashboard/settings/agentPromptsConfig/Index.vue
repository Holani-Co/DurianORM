<script setup>
// Agent Prompts settings screen. Reads the live per-vertical guidance from the
// zoho-bridge (via the admin Rails proxy) and lets administrators tune, per
// vertical, which store details the social agent surfaces and how it behaves —
// with version history + rollback. Two tabs: Prompts, History.
import { ref, computed, onMounted, defineAsyncComponent } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';

const PromptsEditor = defineAsyncComponent(() => import('./PromptsEditor.vue'));
const HistoryPanel = defineAsyncComponent(() => import('./HistoryPanel.vue'));

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const loading = ref(true);
const error = ref(false);
const data = ref(null);
const activeTab = ref('prompts');

const fetchConfig = async () => {
  loading.value = true;
  error.value = false;
  try {
    const { data: res } = await axios.get(
      `/api/v1/accounts/${accountId.value}/integrations/agent_prompts_config`
    );
    data.value = res;
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
};

onMounted(fetchConfig);

const override = computed(() => data.value?.override || {});
const activeVersion = computed(() => data.value?.active_version || null);
const verticals = computed(() => data.value?.verticals || []);
const maxChars = computed(() => data.value?.max_chars || 1500);
const enabled = computed(() => Boolean(data.value?.enabled));

const tabs = computed(() => [
  { key: 'prompts', label: t('AGENT_PROMPTS_CONFIG.TABS.PROMPTS') },
  { key: 'history', label: t('AGENT_PROMPTS_CONFIG.TABS.HISTORY') },
]);
</script>

<template>
  <div class="w-full">
    <div class="mb-6">
      <h1 class="text-xl font-semibold text-n-slate-12">
        {{ t('AGENT_PROMPTS_CONFIG.HEADER') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('AGENT_PROMPTS_CONFIG.DESCRIPTION') }}
      </p>
    </div>

    <div v-if="loading" class="py-10 text-sm text-n-slate-11">
      {{ t('AGENT_PROMPTS_CONFIG.LOADING') }}
    </div>

    <div
      v-else-if="error"
      class="p-4 text-sm border rounded-lg border-n-weak bg-n-alpha-2 text-n-ruby-11"
    >
      {{ t('AGENT_PROMPTS_CONFIG.BRIDGE_UNAVAILABLE') }}
    </div>

    <div v-else>
      <div class="flex flex-wrap items-center gap-2 mb-4">
        <span
          v-if="enabled"
          class="px-2 py-0.5 text-xs font-medium rounded-full bg-n-teal-3 text-n-teal-11"
        >
          {{ t('AGENT_PROMPTS_CONFIG.LIVE_BADGE') }}
        </span>
        <span
          v-else
          class="px-2 py-0.5 text-xs font-medium rounded-full bg-n-amber-3 text-n-amber-11"
        >
          {{ t('AGENT_PROMPTS_CONFIG.DISABLED_BADGE') }}
        </span>
        <span v-if="activeVersion" class="text-xs text-n-slate-11">
          {{
            t('AGENT_PROMPTS_CONFIG.VERSION_INFO', {
              version: activeVersion.id,
              actor: activeVersion.created_by || '—',
              when: activeVersion.created_at,
            })
          }}
        </span>
        <span v-else class="text-xs text-n-slate-11">
          {{ t('AGENT_PROMPTS_CONFIG.NO_OVERRIDE') }}
        </span>
      </div>

      <div class="flex gap-1 mb-4 border-b border-n-weak">
        <button
          v-for="tab in tabs"
          :key="tab.key"
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

      <div v-if="activeTab === 'prompts'">
        <PromptsEditor
          :override="override"
          :verticals="verticals"
          :max-chars="maxChars"
          :enabled="enabled"
          @published="fetchConfig"
        />
      </div>

      <div v-else-if="activeTab === 'history'">
        <HistoryPanel @restored="fetchConfig" />
      </div>
    </div>
  </div>
</template>
