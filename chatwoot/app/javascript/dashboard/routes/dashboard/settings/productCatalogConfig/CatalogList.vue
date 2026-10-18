<script setup>
// Everything in the live price list: search, page through, and download it as
// a CSV in the template format (the starting point for a Google Sheet).
import { ref, computed, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  products: { type: Array, default: () => [] },
});

const PAGE_SIZE = 50;

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const query = ref('');
const page = ref(0);
const downloading = ref(false);

const rupees = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});
const price = v => (typeof v === 'number' && v > 0 ? rupees.format(v) : '—');

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.products;
  return props.products.filter(p =>
    [p.sku, p.name, p.category, p.family].some(v =>
      String(v || '')
        .toLowerCase()
        .includes(q)
    )
  );
});

watch(query, () => {
  page.value = 0;
});

const pageCount = computed(() =>
  Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE))
);
const rows = computed(() =>
  filtered.value.slice(page.value * PAGE_SIZE, (page.value + 1) * PAGE_SIZE)
);
const rangeFrom = computed(() =>
  filtered.value.length ? page.value * PAGE_SIZE + 1 : 0
);
const rangeTo = computed(() =>
  Math.min((page.value + 1) * PAGE_SIZE, filtered.value.length)
);

async function download() {
  if (downloading.value) return;
  downloading.value = true;
  try {
    const { data } = await axios.get(
      `/api/v1/accounts/${accountId.value}/integrations/product_catalog_config/export`
    );
    const blob = new Blob([data.csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = data.filename || 'price-list.csv';
    link.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    useAlert(t('PRODUCT_CATALOG_CONFIG.LIST.DOWNLOAD_FAILED'));
  } finally {
    downloading.value = false;
  }
}
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-3 mb-3">
      <input
        v-model="query"
        type="search"
        :placeholder="t('PRODUCT_CATALOG_CONFIG.LIST.SEARCH_PLACEHOLDER')"
        class="flex-1 min-w-[16rem] px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12"
      />
      <button
        type="button"
        class="px-3 py-2 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1 disabled:opacity-60"
        :disabled="downloading"
        @click="download"
      >
        {{ t('PRODUCT_CATALOG_CONFIG.LIST.DOWNLOAD') }}
      </button>
    </div>

    <div class="overflow-x-auto border rounded-xl border-n-weak">
      <table class="min-w-full text-sm">
        <thead class="bg-n-alpha-1 text-n-slate-11">
          <tr>
            <th class="px-3 py-2 font-medium text-left">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.SKU') }}
            </th>
            <th class="px-3 py-2 font-medium text-left">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.NAME') }}
            </th>
            <th class="px-3 py-2 font-medium text-left">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.CATEGORY') }}
            </th>
            <th class="px-3 py-2 font-medium text-right">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.MRP') }}
            </th>
            <th class="px-3 py-2 font-medium text-right">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.SALE_PRICE') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in rows"
            :key="p.sku"
            class="border-t border-n-weak text-n-slate-12"
          >
            <td class="px-3 py-2 font-mono text-xs whitespace-nowrap">
              {{ p.sku }}
            </td>
            <td class="px-3 py-2">{{ p.name || '—' }}</td>
            <td class="px-3 py-2 text-n-slate-11">{{ p.category || '—' }}</td>
            <td class="px-3 py-2 text-right text-n-slate-11 whitespace-nowrap">
              {{ price(p.mrp) }}
            </td>
            <td class="px-3 py-2 font-medium text-right whitespace-nowrap">
              {{ price(p.sale_price) }}
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="5" class="px-3 py-6 text-center text-n-slate-11">
              {{ t('PRODUCT_CATALOG_CONFIG.LIST.EMPTY') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="filtered.length"
      class="flex items-center justify-between mt-3 text-xs text-n-slate-11"
    >
      <span>
        {{
          t('PRODUCT_CATALOG_CONFIG.LIST.SHOWING', {
            from: rangeFrom,
            to: rangeTo,
            total: filtered.length,
          })
        }}
      </span>
      <span class="flex gap-2">
        <button
          type="button"
          class="px-2 py-1 border rounded-lg border-n-weak disabled:opacity-50"
          :disabled="page === 0"
          @click="page -= 1"
        >
          {{ t('PRODUCT_CATALOG_CONFIG.LIST.PREV') }}
        </button>
        <button
          type="button"
          class="px-2 py-1 border rounded-lg border-n-weak disabled:opacity-50"
          :disabled="page >= pageCount - 1"
          @click="page += 1"
        >
          {{ t('PRODUCT_CATALOG_CONFIG.LIST.NEXT') }}
        </button>
      </span>
    </div>
  </div>
</template>
