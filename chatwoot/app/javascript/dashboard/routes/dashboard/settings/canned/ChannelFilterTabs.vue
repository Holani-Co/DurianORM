<script setup>
// Durian — channel filter bar for the Canned Responses list. Groups templates
// by the channel segment of their short_code (see helper/templateTaxonomy.js).
// Composes with the existing search + sort: this only narrows which rows show.
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  TEMPLATE_CHANNELS,
  OTHER_CHANNEL,
  parseShortCode,
} from 'dashboard/helper/templateTaxonomy';

const props = defineProps({
  modelValue: { type: String, default: 'all' },
  records: { type: Array, default: () => [] },
});
const emit = defineEmits(['update:modelValue']);

const { t } = useI18n();

// channel id → count, derived live from the records' short_codes.
const counts = computed(() => {
  const map = { all: props.records.length };
  props.records.forEach(r => {
    const { channel } = parseShortCode(r.short_code || '');
    map[channel] = (map[channel] || 0) + 1;
  });
  return map;
});

// Always show "All" + the known channels; show "Other" only when it has items.
const tabs = computed(() => {
  const list = [{ id: 'all', label: t('CANNED_MGMT.CHANNEL_FILTER.ALL') }];
  TEMPLATE_CHANNELS.forEach(c => list.push({ id: c.id, label: c.label }));
  if (counts.value[OTHER_CHANNEL]) {
    list.push({
      id: OTHER_CHANNEL,
      label: t('CANNED_MGMT.CHANNEL_FILTER.OTHER'),
    });
  }
  return list;
});

const select = id => emit('update:modelValue', id);
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 mb-4">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      class="flex items-center gap-1.5 px-3 py-1 text-sm font-medium border rounded-lg"
      :class="
        modelValue === tab.id
          ? 'bg-n-solid-blue text-n-slate-12 border-n-brand'
          : 'bg-n-solid-2 text-n-slate-11 border-n-weak hover:bg-n-solid-3'
      "
      @click="select(tab.id)"
    >
      {{ tab.label }}
      <span class="text-xs text-n-slate-10">{{ counts[tab.id] || 0 }}</span>
    </button>
  </div>
</template>
