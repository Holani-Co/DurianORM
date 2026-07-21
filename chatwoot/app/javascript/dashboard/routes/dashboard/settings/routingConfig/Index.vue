<script setup>
// Routing-config settings screen. Reads the live email-routing rules from the
// zoho-bridge (via the admin Rails proxy) and shows them across three tabs.
// Categories and Thresholds are read-only for now; CRM Owners is editable
// (Phase 2) — reassign a territory's owner and publish, live in seconds.
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import CategoriesEditor from './CategoriesEditor.vue';
import CrmOwnersEditor from './CrmOwnersEditor.vue';
import PreviewPanel from './PreviewPanel.vue';
import ThresholdsEditor from './ThresholdsEditor.vue';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const loading = ref(true);
const error = ref(false);
const data = ref(null);
const activeTab = ref('categories');

const fetchConfig = async () => {
  loading.value = true;
  error.value = false;
  try {
    const { data: res } = await axios.get(
      `/api/v1/accounts/${accountId.value}/integrations/routing_config`
    );
    data.value = res;
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
};

onMounted(fetchConfig);

const effective = computed(() => data.value?.effective || {});
const override = computed(() => data.value?.override || {});
const activeVersion = computed(() => data.value?.active_version || null);
const knownOwners = computed(() => data.value?.known_owners || []);
const cacheTtl = computed(() => data.value?.cache_ttl_seconds);

const tabs = computed(() => [
  { key: 'categories', label: t('ROUTING_CONFIG.TABS.CATEGORIES') },
  { key: 'owners', label: t('ROUTING_CONFIG.TABS.OWNERS') },
  { key: 'settings', label: t('ROUTING_CONFIG.TABS.SETTINGS') },
  { key: 'preview', label: t('ROUTING_CONFIG.TABS.PREVIEW') },
]);
</script>

<template>
  <div class="w-full">
    <div class="mb-6">
      <h1 class="text-xl font-semibold text-n-slate-12">
        {{ t('ROUTING_CONFIG.HEADER') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('ROUTING_CONFIG.DESCRIPTION') }}
      </p>
    </div>

    <div v-if="loading" class="py-10 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.LOADING') }}
    </div>

    <div
      v-else-if="error"
      class="p-4 text-sm border rounded-lg border-n-weak bg-n-alpha-2 text-n-ruby-11"
    >
      {{ t('ROUTING_CONFIG.BRIDGE_UNAVAILABLE') }}
    </div>

    <div v-else>
      <div class="flex flex-wrap items-center gap-2 mb-4">
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full bg-n-teal-3 text-n-teal-11"
        >
          {{ t('ROUTING_CONFIG.LIVE_BADGE') }}
        </span>
        <span v-if="activeVersion" class="text-xs text-n-slate-11">
          {{
            t('ROUTING_CONFIG.VERSION_INFO', {
              version: activeVersion.id,
              actor: activeVersion.created_by || '—',
              when: activeVersion.created_at,
            })
          }}
        </span>
        <span v-else class="text-xs text-n-slate-11">
          {{ t('ROUTING_CONFIG.NO_OVERRIDE') }}
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

      <div v-if="activeTab === 'categories'">
        <CategoriesEditor
          :effective="effective"
          :override="override"
          @published="fetchConfig"
        />
      </div>

      <div v-else-if="activeTab === 'owners'">
        <CrmOwnersEditor
          :effective="effective"
          :override="override"
          :known-owners="knownOwners"
          @published="fetchConfig"
        />
      </div>

      <div v-else-if="activeTab === 'settings'">
        <ThresholdsEditor
          :effective="effective"
          :override="override"
          :cache-ttl="cacheTtl"
          @published="fetchConfig"
        />
      </div>

      <div v-else-if="activeTab === 'preview'">
        <PreviewPanel :override="override" />
      </div>
    </div>
  </div>
</template>
