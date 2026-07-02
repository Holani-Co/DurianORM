<script setup>
// Store + star-rating dropdowns shown only in the Google Reviews inbox.
// Selecting a store (and optionally a rating) filters the conversation list
// server-side via the `store-<name>` / `review-<n>star` labels the
// reviews poller applies. Presentational only — the parent (ChatList) owns
// the actual filter dispatch.
defineProps({
  // [{ value: 'store-koramangala', label: 'Koramangala' }, …]
  storeOptions: { type: Array, default: () => [] },
  // [{ value: 'replied-by-3', label: 'Aditya' }, …]
  agentOptions: { type: Array, default: () => [] },
  store: { type: String, default: '' },
  rating: { type: String, default: '' },
  reply: { type: String, default: '' },
  agent: { type: String, default: '' },
  sort: { type: String, default: '' },
  // Review POSTING date range (YYYY-MM-DD) — filters on the review's actual
  // Google date (additional_attributes.review_created_at), not ingestion date.
  dateFrom: { type: String, default: '' },
  dateTo: { type: String, default: '' },
});

const emit = defineEmits([
  'update:store',
  'update:rating',
  'update:reply',
  'update:agent',
  'update:sort',
  'update:dateFrom',
  'update:dateTo',
  'change',
]);

// Client-side sort of the loaded list (no server round-trip → no 'change').
const SORT_OPTIONS = [
  { value: '', label: 'Sort: Latest ingested' },
  { value: 'review_date', label: 'Sort: Review date' },
];

const ALL_STORES_LABEL = 'All stores';

const RATING_OPTIONS = [
  { value: '', label: 'All ratings' },
  { value: 'review-5star', label: '★★★★★  (5)' },
  { value: 'review-4star', label: '★★★★  (4)' },
  { value: 'review-3star', label: '★★★  (3)' },
  { value: 'review-2star', label: '★★  (2)' },
  { value: 'review-1star', label: '★  (1)' },
  { value: 'review-unrated', label: 'Unrated' },
];

// Reply-status / reply-type — backed by the `review-*` labels the bridge
// applies (reviews_poller.tag_reply_status).
const REPLY_OPTIONS = [
  { value: '', label: 'All replies' },
  { value: 'review-unreplied', label: 'Unreplied' },
  { value: 'review-replied', label: 'Replied (any)' },
  { value: 'review-auto-replied', label: 'Auto-replied' },
  { value: 'review-manually-replied', label: 'Manually replied' },
];

const onStore = e => {
  emit('update:store', e.target.value);
  emit('change');
};
const onRating = e => {
  emit('update:rating', e.target.value);
  emit('change');
};
const onReply = e => {
  emit('update:reply', e.target.value);
  emit('change');
};
const onAgent = e => {
  emit('update:agent', e.target.value);
  emit('change');
};
const onSort = e => {
  emit('update:sort', e.target.value);
};
// Client-side like sort — no 'change' (no server round-trip).
const onDateFrom = e => {
  emit('update:dateFrom', e.target.value);
};
const onDateTo = e => {
  emit('update:dateTo', e.target.value);
};

const selectClass =
  'w-full min-w-0 px-2 py-1 text-sm rounded-md cursor-pointer bg-n-alpha-2 text-n-slate-12 border border-n-weak focus:outline-none focus:border-n-brand';
</script>

<template>
  <div class="grid grid-cols-2 gap-2 px-3 py-2">
    <select :value="store" :class="selectClass" @change="onStore">
      <option value="">{{ ALL_STORES_LABEL }}</option>
      <option v-for="opt in storeOptions" :key="opt.value" :value="opt.value">
        {{ opt.label }}
      </option>
    </select>
    <select :value="rating" :class="selectClass" @change="onRating">
      <option
        v-for="opt in RATING_OPTIONS"
        :key="opt.value || 'all'"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
    <select :value="reply" :class="selectClass" @change="onReply">
      <option
        v-for="opt in REPLY_OPTIONS"
        :key="opt.value || 'all-replies'"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
    <select :value="agent" :class="selectClass" @change="onAgent">
      <option value="">All agents</option>
      <option v-for="opt in agentOptions" :key="opt.value" :value="opt.value">
        {{ opt.label }}
      </option>
    </select>
    <select :value="sort" :class="[selectClass, 'col-span-2']" @change="onSort">
      <option
        v-for="opt in SORT_OPTIONS"
        :key="opt.value || 'default-sort'"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
    <label class="flex flex-col gap-0.5 min-w-0">
      <span class="text-xs text-n-slate-11">Reviewed from</span>
      <input
        type="date"
        :value="dateFrom"
        :class="selectClass"
        @change="onDateFrom"
      />
    </label>
    <label class="flex flex-col gap-0.5 min-w-0">
      <span class="text-xs text-n-slate-11">Reviewed to</span>
      <input
        type="date"
        :value="dateTo"
        :class="selectClass"
        @change="onDateTo"
      />
    </label>
  </div>
</template>
