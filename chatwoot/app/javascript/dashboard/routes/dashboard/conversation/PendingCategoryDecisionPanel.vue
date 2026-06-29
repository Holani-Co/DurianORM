<script setup>
// Renders when zoho-bridge classified an email but with LOW confidence, so it
// did NOT auto-forward. State comes from
// `custom_attributes.pending_category_decision` (written by the bridge):
//   {
//     suggested, suggested_display, confidence, reason,
//     alternatives: [{ category, display_name, confidence }, ...],
//     categories:   [{ category, display_name }, ...]   // full dropdown
//   }
// The agent confirms the AI's pick (or chooses another), and on click we POST
// to the Rails proxy which forwards to the bridge — which then runs the real
// forward/route for the chosen category.
import { computed, ref } from 'vue';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  pending: { type: Object, default: () => ({}) },
  conversationId: { type: [Number, String], required: true },
});

const emit = defineEmits(['resolved']);

const axios = window.axios;

const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');

const submitting = ref(false);
const selected = ref(props.pending?.suggested || '');

const suggestedDisplay = computed(() => props.pending?.suggested_display || '');
const confidencePct = computed(() =>
  Math.round((props.pending?.confidence || 0) * 100)
);
const reason = computed(() => props.pending?.reason || '');
const alternatives = computed(() => props.pending?.alternatives || []);
const categories = computed(() => props.pending?.categories || []);

// Bulk orders also need the buyer sector (government/private) → show a sector
// picker whenever the category being confirmed is project_bulk_order.
const BULK_CATEGORY = 'project_bulk_order';
const SECTOR_OPTIONS = [
  { value: 'government', label: 'Government / Public-sector' },
  { value: 'private', label: 'Private company' },
];
const selectedSector = ref(props.pending?.sector_suggested || 'private');
const sectorReason = computed(() => props.pending?.sector_reason || '');
const sectorConfidencePct = computed(() =>
  Math.round((props.pending?.sector_confidence || 0) * 100)
);
const showSectorPicker = computed(
  () => props.pending?.needs_sector || selected.value === BULK_CATEGORY
);

async function confirm(category) {
  if (submitting.value || !category) return;
  if (category === BULK_CATEGORY && !selectedSector.value) {
    useAlert('Pick the buyer sector (government or private) first.');
    return;
  }
  submitting.value = true;
  try {
    await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/resolve_category_decision`,
      {
        conversation_id: Number(props.conversationId),
        category,
        ...(category === BULK_CATEGORY ? { sector: selectedSector.value } : {}),
      }
    );
    // Bridge clears pending_category_decision server-side; mirror locally so
    // the panel disappears without waiting for a websocket push.
    await store.dispatch('updateCustomAttributes', {
      conversationId: Number(props.conversationId),
      customAttributes: { pending_category_decision: null },
    });
    useAlert('Category confirmed — forwarding and routing now.');
    emit('resolved', { category });
  } catch (e) {
    useAlert(
      e?.response?.data?.error ||
        e?.response?.data?.detail ||
        'Could not confirm the category. Please try again.'
    );
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="px-1 text-xs text-n-slate-11">
      <span class="text-n-slate-12">Low confidence — confirm the category</span>
      <span v-if="suggestedDisplay" class="text-n-slate-10">
        · best guess {{ suggestedDisplay }} ({{ confidencePct }}%)
      </span>
    </div>
    <p v-if="reason" class="px-1 text-xs text-n-slate-11 italic">
      {{ reason }}
    </p>

    <!-- Buyer sector (bulk orders only). Sent with the confirm when the chosen
         category is Project / Bulk Order, routing to that sector's handler. -->
    <div
      v-if="showSectorPicker"
      class="flex flex-col gap-1.5 p-2.5 rounded-md bg-n-alpha-1"
    >
      <span class="text-xs font-medium text-n-slate-12">
        Buyer sector
        <span v-if="sectorConfidencePct" class="font-normal text-n-slate-10">
          · AI guess {{ sectorConfidencePct }}%
        </span>
      </span>
      <p v-if="sectorReason" class="text-xs text-n-slate-11 italic">
        {{ sectorReason }}
      </p>
      <div class="flex items-center gap-2">
        <button
          v-for="opt in SECTOR_OPTIONS"
          :key="opt.value"
          type="button"
          class="flex-1 px-2 py-1.5 text-xs font-medium rounded-md border"
          :class="
            selectedSector === opt.value
              ? 'bg-n-brand text-white border-n-brand'
              : 'bg-n-background text-n-slate-12 border-n-weak hover:bg-n-alpha-2'
          "
          @click="selectedSector = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <!-- AI's top pick + ranked alternatives as one-click buttons -->
    <div class="flex flex-col gap-2">
      <button
        type="button"
        class="flex items-center justify-between gap-2 p-2.5 rounded-md bg-n-solid-blue text-n-slate-12 disabled:opacity-50"
        :disabled="submitting"
        @click="confirm(pending.suggested)"
      >
        <span class="text-sm font-medium">
          <span class="i-ph-sparkle-fill align-middle text-n-iris-11" />
          {{ suggestedDisplay }}
        </span>
        <span class="text-xs text-n-slate-11">{{ confidencePct }}%</span>
      </button>
      <button
        v-for="alt in alternatives"
        :key="alt.category"
        type="button"
        class="flex items-center justify-between gap-2 p-2.5 rounded-md bg-n-alpha-1 text-n-slate-12 hover:bg-n-alpha-2 disabled:opacity-50"
        :disabled="submitting"
        @click="confirm(alt.category)"
      >
        <span class="text-sm">{{ alt.display_name }}</span>
        <span class="text-xs text-n-slate-10">
          {{ Math.round((alt.confidence || 0) * 100) }}%
        </span>
      </button>
    </div>

    <!-- Full dropdown for anything not in the top picks -->
    <div class="flex items-center gap-2 px-1">
      <select
        v-model="selected"
        class="flex-1 px-2 py-1.5 text-sm rounded-md bg-n-background text-n-slate-12 border border-n-weak"
        :disabled="submitting"
      >
        <option v-for="c in categories" :key="c.category" :value="c.category">
          {{ c.display_name }}
        </option>
      </select>
      <button
        type="button"
        class="px-3 py-1.5 text-xs font-medium text-white rounded-md bg-n-brand hover:opacity-90 disabled:opacity-50"
        :disabled="submitting || !selected"
        @click="confirm(selected)"
      >
        {{ submitting ? 'Confirming…' : 'Confirm' }}
      </button>
    </div>
  </div>
</template>
