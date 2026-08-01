<script setup>
// Durian — Vertical + Category sub-filters for the Google Reviews canned
// responses. Both dropdowns are DERIVED from the review records' short_codes
// (review_<vertical>_<case>_NN), so nothing needs to stay in sync with the
// reply-bank YAML. Client-side only; composes with the channel tab + search.
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import Select from 'dashboard/components-next/select/Select.vue';
import {
  REVIEW_VERTICALS,
  parseReviewCode,
  prettyReviewCategory,
} from 'dashboard/helper/templateTaxonomy';

const props = defineProps({
  // v-model:vertical / v-model:category — the two active selections ('all' = off).
  vertical: { type: String, default: 'all' },
  category: { type: String, default: 'all' },
  // The review-channel records only (already narrowed to the Google Reviews tab).
  records: { type: Array, default: () => [] },
});
const emit = defineEmits(['update:vertical', 'update:category']);

const { t } = useI18n();

const allOption = computed(() => ({
  value: 'all',
  label: t('CANNED_MGMT.REVIEW_FILTER.ALL'),
}));

// Only offer verticals that actually have records, so the dropdown never lists
// an empty option.
const verticalOptions = computed(() => {
  const present = new Set(
    props.records
      .map(r => parseReviewCode(r.short_code || '').vertical)
      .filter(Boolean)
  );
  return [
    allOption.value,
    ...REVIEW_VERTICALS.filter(v => present.has(v.value)),
  ];
});

// Categories present for the chosen vertical (or across all verticals when the
// vertical filter is off), sorted and prettified for display.
const categoryOptions = computed(() => {
  const set = new Set();
  props.records.forEach(r => {
    const { vertical, category } = parseReviewCode(r.short_code || '');
    if (!category) return;
    if (props.vertical !== 'all' && vertical !== props.vertical) return;
    set.add(category);
  });
  return [
    allOption.value,
    ...[...set].sort().map(c => ({ value: c, label: prettyReviewCategory(c) })),
  ];
});

// Changing the vertical resets the category — a category from another vertical
// would filter everything out.
const verticalModel = computed({
  get: () => props.vertical,
  set: value => {
    emit('update:vertical', value);
    emit('update:category', 'all');
  },
});
const categoryModel = computed({
  get: () => props.category,
  set: value => emit('update:category', value),
});
</script>

<template>
  <div class="flex flex-wrap items-center gap-4 mb-4">
    <div class="flex items-center gap-2">
      <span class="text-sm text-n-slate-11">
        {{ t('CANNED_MGMT.REVIEW_FILTER.VERTICAL') }}
      </span>
      <Select v-model="verticalModel" :options="verticalOptions" />
    </div>
    <div class="flex items-center gap-2">
      <span class="text-sm text-n-slate-11">
        {{ t('CANNED_MGMT.REVIEW_FILTER.CATEGORY') }}
      </span>
      <Select v-model="categoryModel" :options="categoryOptions" />
    </div>
  </div>
</template>
