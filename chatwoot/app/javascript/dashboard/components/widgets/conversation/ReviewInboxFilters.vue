<script setup>
// Store + star-rating dropdowns shown only in the Google Reviews inbox.
// Selecting a store (and optionally a rating) filters the conversation list
// server-side via the `store-<name>` / `review-<n>star` labels the
// reviews poller applies. Presentational only — the parent (ChatList) owns
// the actual filter dispatch.
defineProps({
  // [{ value: 'store-koramangala', label: 'Koramangala' }, …]
  storeOptions: { type: Array, default: () => [] },
  store: { type: String, default: '' },
  rating: { type: String, default: '' },
});

const emit = defineEmits(['update:store', 'update:rating', 'change']);

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

const onStore = e => {
  emit('update:store', e.target.value);
  emit('change');
};
const onRating = e => {
  emit('update:rating', e.target.value);
  emit('change');
};

const selectClass =
  'flex-1 min-w-0 px-2 py-1 text-sm rounded-md cursor-pointer bg-n-alpha-2 text-n-slate-12 border border-n-weak focus:outline-none focus:border-n-brand';
</script>

<template>
  <div class="flex items-center gap-2 px-3 py-2">
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
  </div>
</template>
