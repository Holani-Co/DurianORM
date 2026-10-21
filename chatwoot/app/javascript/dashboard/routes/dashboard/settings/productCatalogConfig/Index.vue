<script setup>
// Product Catalog settings screen (via the admin Rails proxy): the price list
// the social agent uses. Three tabs: Products (what's listed), Update list
// (Google Sheet / file upload), History (rollback).
import { ref, computed, onMounted, defineAsyncComponent } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';

const CatalogList = defineAsyncComponent(() => import('./CatalogList.vue'));
const CatalogUpdate = defineAsyncComponent(() => import('./CatalogUpdate.vue'));
const HistoryPanel = defineAsyncComponent(() => import('./HistoryPanel.vue'));

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const loading = ref(true);
const error = ref(false);
const data = ref(null);
const activeTab = ref('products');

const fetchConfig = async () => {
  loading.value = !data.value;
  error.value = false;
  try {
    const { data: res } = await axios.get(
      `/api/v1/accounts/${accountId.value}/integrations/product_catalog_config`
    );
    data.value = res;
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
};

onMounted(fetchConfig);

const meta = computed(() => data.value?.meta || {});
const products = computed(() => data.value?.products || []);
const sync = computed(() => data.value?.sync || {});
const fresh = computed(() => Boolean(data.value?.fresh));

const formatWhen = iso => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

const sourceLabel = computed(() => {
  if (!meta.value.managed) return t('PRODUCT_CATALOG_CONFIG.SOURCES.BUILTIN');
  return meta.value.source === 'sheet'
    ? t('PRODUCT_CATALOG_CONFIG.SOURCES.SHEET')
    : t('PRODUCT_CATALOG_CONFIG.SOURCES.UPLOAD');
});

const tabs = computed(() => [
  { key: 'products', label: t('PRODUCT_CATALOG_CONFIG.TABS.PRODUCTS') },
  { key: 'update', label: t('PRODUCT_CATALOG_CONFIG.TABS.UPDATE') },
  { key: 'history', label: t('PRODUCT_CATALOG_CONFIG.TABS.HISTORY') },
]);

const onPublished = async () => {
  await fetchConfig();
  activeTab.value = 'products';
};
</script>

<template>
  <div class="w-full">
    <div class="mb-6">
      <h1 class="text-xl font-semibold text-n-slate-12">
        {{ t('PRODUCT_CATALOG_CONFIG.HEADER') }}
      </h1>
      <p class="mt-1 text-sm text-n-slate-11">
        {{ t('PRODUCT_CATALOG_CONFIG.DESCRIPTION') }}
      </p>
    </div>

    <div v-if="loading" class="py-10 text-sm text-n-slate-11">
      {{ t('PRODUCT_CATALOG_CONFIG.LOADING') }}
    </div>

    <div
      v-else-if="error"
      class="p-4 text-sm border rounded-lg border-n-weak bg-n-alpha-2 text-n-ruby-11"
    >
      {{ t('PRODUCT_CATALOG_CONFIG.BRIDGE_UNAVAILABLE') }}
    </div>

    <div v-else>
      <div class="flex flex-wrap items-center gap-2 mb-2">
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full"
          :class="
            fresh ? 'bg-n-teal-3 text-n-teal-11' : 'bg-n-amber-3 text-n-amber-11'
          "
        >
          {{ sourceLabel }}
        </span>
        <span class="text-xs text-n-slate-11">
          {{
            t('PRODUCT_CATALOG_CONFIG.SUMMARY.COUNT', { count: meta.count })
          }}
        </span>
        <span v-if="meta.price_period" class="text-xs text-n-slate-11">
          ·
          {{
            t('PRODUCT_CATALOG_CONFIG.SUMMARY.PERIOD', {
              period: meta.price_period,
            })
          }}
        </span>
        <span v-if="meta.managed" class="text-xs text-n-slate-11">
          ·
          {{
            t('PRODUCT_CATALOG_CONFIG.SUMMARY.PUBLISHED', {
              version: meta.version_id,
              actor: meta.published_by || '—',
              when: formatWhen(meta.published_at),
            })
          }}
        </span>
      </div>

      <p
        v-if="!meta.managed"
        class="p-3 mb-4 text-xs border rounded-lg border-n-weak bg-n-amber-2 text-n-amber-11"
      >
        {{
          t('PRODUCT_CATALOG_CONFIG.SUMMARY.BUILTIN_WARNING', {
            period: meta.price_period || '—',
          })
        }}
      </p>
      <p
        v-else-if="!fresh"
        class="p-3 mb-4 text-xs border rounded-lg border-n-weak bg-n-amber-2 text-n-amber-11"
      >
        {{
          t('PRODUCT_CATALOG_CONFIG.SUMMARY.STALE_WARNING', {
            days: data.quote_max_age_days,
          })
        }}
      </p>
      <p v-else class="mb-4 text-xs text-n-slate-11">
        {{ t('PRODUCT_CATALOG_CONFIG.SUMMARY.FRESH_NOTE') }}
      </p>

      <div class="flex gap-1 mb-4 border-b border-n-weak">
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

      <CatalogList v-if="activeTab === 'products'" :products="products" />
      <CatalogUpdate
        v-else-if="activeTab === 'update'"
        :sync="sync"
        @published="onPublished"
        @changed="fetchConfig"
      />
      <HistoryPanel v-else @restored="fetchConfig" />
    </div>
  </div>
</template>
