<script setup>
// Routing-config settings screen. Reads the live email-routing rules from the
// zoho-bridge (via the admin Rails proxy) and shows them across three tabs.
// Categories and Thresholds are read-only for now; CRM Owners is editable
// (Phase 2) — reassign a territory's owner and publish, live in seconds.
import { ref, computed, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import CrmOwnersEditor from './CrmOwnersEditor.vue';
import PreviewPanel from './PreviewPanel.vue';

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

const categories = computed(() => {
  const cats = effective.value.categories || {};
  return Object.keys(cats).map(key => {
    const c = cats[key] || {};
    const keywords = c.keywords || [];
    const shown = keywords.slice(0, 6);
    const extra = keywords.length - shown.length;
    return {
      key,
      displayName: c.display_name || key,
      action: c.action || 'in_channel',
      forwardTo: c.forward_to || '',
      keywordsLabel: shown.length
        ? shown.join(', ') + (extra > 0 ? ` +${extra}` : '')
        : '',
    };
  });
});

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

      <div
        v-if="activeTab === 'categories' || activeTab === 'settings'"
        class="p-3 mb-4 text-xs border rounded-lg border-n-amber-6 bg-n-amber-2 text-n-amber-12"
      >
        {{ t('ROUTING_CONFIG.READ_ONLY_NOTE') }}
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
        <p class="mb-3 text-sm text-n-slate-11">
          {{
            t('ROUTING_CONFIG.CATEGORIES.SUBTITLE', {
              count: categories.length,
            })
          }}
        </p>
        <table class="w-full text-sm border rounded-lg border-n-weak">
          <thead>
            <tr class="text-left text-n-slate-11 bg-n-alpha-1">
              <th class="px-3 py-2 font-medium">
                {{ t('ROUTING_CONFIG.CATEGORIES.COL_NAME') }}
              </th>
              <th class="px-3 py-2 font-medium">
                {{ t('ROUTING_CONFIG.CATEGORIES.COL_ACTION') }}
              </th>
              <th class="px-3 py-2 font-medium">
                {{ t('ROUTING_CONFIG.CATEGORIES.COL_FORWARD') }}
              </th>
              <th class="px-3 py-2 font-medium">
                {{ t('ROUTING_CONFIG.CATEGORIES.COL_KEYWORDS') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in categories"
              :key="c.key"
              class="border-t border-n-weak"
            >
              <td class="px-3 py-2 align-top text-n-slate-12">
                <div class="font-medium">{{ c.displayName }}</div>
                <div class="text-xs text-n-slate-10">{{ c.key }}</div>
              </td>
              <td class="px-3 py-2 align-top">
                <span
                  :class="
                    c.action === 'forward'
                      ? 'text-n-amber-11'
                      : 'text-n-teal-11'
                  "
                >
                  {{
                    c.action === 'forward'
                      ? t('ROUTING_CONFIG.CATEGORIES.ACTION_FORWARD')
                      : t('ROUTING_CONFIG.CATEGORIES.ACTION_IN_CHANNEL')
                  }}
                </span>
              </td>
              <td class="px-3 py-2 align-top text-n-slate-11">
                {{ c.forwardTo || t('ROUTING_CONFIG.CATEGORIES.NONE') }}
              </td>
              <td class="px-3 py-2 text-xs align-top text-n-slate-10">
                {{ c.keywordsLabel || t('ROUTING_CONFIG.CATEGORIES.NONE') }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else-if="activeTab === 'owners'">
        <CrmOwnersEditor
          :effective="effective"
          :override="override"
          :known-owners="knownOwners"
          @published="fetchConfig"
        />
      </div>

      <div v-else-if="activeTab === 'settings'" class="text-sm">
        <dl class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div class="p-3 border rounded-lg border-n-weak">
            <dt class="text-xs text-n-slate-10">
              {{ t('ROUTING_CONFIG.SETTINGS.CONFIDENCE') }}
            </dt>
            <dd class="mt-1 font-medium text-n-slate-12">
              {{ effective.confidence_threshold ?? '—' }}
            </dd>
          </div>
          <div class="p-3 border rounded-lg border-n-weak">
            <dt class="text-xs text-n-slate-10">
              {{ t('ROUTING_CONFIG.SETTINGS.CACHE_TTL') }}
            </dt>
            <dd class="mt-1 font-medium text-n-slate-12">
              {{ cacheTtl ?? '—' }}
            </dd>
          </div>
        </dl>
      </div>

      <div v-else-if="activeTab === 'preview'">
        <PreviewPanel :override="override" />
      </div>
    </div>
  </div>
</template>
