<script setup>
// Durian — Vertical + Category sub-filters for the Google Reviews canned
// responses. Both dropdowns are DERIVED from the review records' short_codes
// (review_<vertical>_<case>_NN), so nothing needs to stay in sync with the
// reply-bank YAML. Client-side only; composes with the channel tab + search.
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
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

// Only offer verticals that actually have records, so the dropdown never lists
// an empty option.
const verticalOptions = computed(() => {
  const present = new Set(
    props.records
      .map(r => parseReviewCode(r.short_code || '').vertical)
      .filter(Boolean)
  );
  return REVIEW_VERTICALS.filter(v => present.has(v.value));
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
  return [...set]
    .sort()
    .map(c => ({ value: c, label: prettyReviewCategory(c) }));
});

// Changing the vertical resets the category — a category from another vertical
// would filter everything out.
const onVertical = e => {
  emit('update:vertical', e.target.value);
  emit('update:category', 'all');
};
const onCategory = e => emit('update:category', e.target.value);

const selectClass =
  'px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand';
</script>

<template>
  <div class="flex flex-wrap items-center gap-4 mb-4">
    <label class="flex items-center gap-2">
      <span class="text-xs font-medium text-n-slate-11">
        {{ t('CANNED_MGMT.REVIEW_FILTER.VERTICAL') }}
      </span>
      <select :value="vertical" :class="selectClass" @change="onVertical">
        <option value="all">{{ t('CANNED_MGMT.REVIEW_FILTER.ALL') }}</option>
        <option v-for="v in verticalOptions" :key="v.value" :value="v.value">
          {{ v.label }}
        </option>
      </select>
    </label>
    <label class="flex items-center gap-2">
      <span class="text-xs font-medium text-n-slate-11">
        {{ t('CANNED_MGMT.REVIEW_FILTER.CATEGORY') }}
      </span>
      <select :value="category" :class="selectClass" @change="onCategory">
        <option value="all">{{ t('CANNED_MGMT.REVIEW_FILTER.ALL') }}</option>
        <option v-for="c in categoryOptions" :key="c.value" :value="c.value">
          {{ c.label }}
        </option>
      </select>
    </label>
  </div>
</template>
