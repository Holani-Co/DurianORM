<script setup>
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
import { useStore } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import { frontendURL } from 'dashboard/helper/URLHelper';

// Duplicate-contact suggestions written by the zoho-bridge sidecar into
// `custom_attributes.identity_matches`. Each entry is the SAME PERSON
// reaching out on another channel, scored 0-100 with the evidence behind the
// score. The bridge never merges — that's a human decision here, because
// Chatwoot's merge deletes the absorbed contact (partially irreversible).
//
// Match entry shape (see zoho-bridge/identity_matcher.find_matches):
//   { contact_id, name, email, phone, score, band,
//     matched: [{type,label,detail}], mismatched: [{type,label}], note }
const props = defineProps({
  matches: {
    type: Array,
    default: () => [],
  },
  // The conversation's current contact — becomes the "child" (absorbed) side
  // of a merge, matching Chatwoot's own ContactMergeModal convention (the
  // contact you're looking at is the one folded into the chosen survivor).
  currentContactId: {
    type: [Number, String],
    default: null,
  },
  currentContactName: {
    type: String,
    default: '',
  },
});

const route = useRoute();
const store = useStore();

// Locally hide cards the agent has merged/dismissed this session so the
// stale custom_attribute entry doesn't keep showing a now-merged contact.
const dismissed = ref(new Set());
const mergingId = ref(null);

const visibleMatches = computed(() =>
  (props.matches || []).filter(m => !dismissed.value.has(m.contact_id))
);

const bandLabel = band => {
  if (band === 'near_certain') return 'Near-certain match';
  if (band === 'likely') return 'Likely match';
  if (band === 'possible') return 'Possible match';
  return 'Match';
};

// Confidence-bar + label colour by band. Green only for near-certain so a
// glance reads the risk correctly; amber for likely; muted for possible.
const barColorClass = band => {
  if (band === 'near_certain') return 'bg-emerald-500';
  if (band === 'likely') return 'bg-amber-500';
  return 'bg-n-slate-9';
};
const labelColorClass = band => {
  if (band === 'near_certain') return 'text-emerald-600 dark:text-emerald-400';
  if (band === 'likely') return 'text-amber-600 dark:text-amber-400';
  return 'text-n-slate-11';
};

const contactUrl = contactId =>
  frontendURL(`accounts/${route.params.accountId}/contacts/${contactId}`);

const clampedScore = score => Math.max(0, Math.min(100, Number(score) || 0));

const onMerge = async match => {
  const survivor = match.name || `contact #${match.contact_id}`;
  const absorbed = props.currentContactName || 'this contact';
  // Spell out the direction AND the irreversibility — merge deletes the
  // absorbed contact. Child = current contact, parent = the match (Chatwoot's
  // own convention: the viewed contact is folded into the chosen survivor).
  const ok = window.confirm(
    `Merge "${absorbed}" into "${survivor}"?\n\n` +
      `"${absorbed}" will be deleted and all its conversations moved to ` +
      `"${survivor}". This cannot be undone.`
  );
  if (!ok) return;

  mergingId.value = match.contact_id;
  try {
    await store.dispatch('contacts/merge', {
      childId: props.currentContactId,
      parentId: match.contact_id,
    });
    useAlert(`Merged into ${survivor}.`);
    dismissed.value = new Set([...dismissed.value, match.contact_id]);
  } catch (error) {
    useAlert('Could not merge contacts. Please try again.');
  } finally {
    mergingId.value = null;
  }
};
</script>

<template>
  <div v-if="visibleMatches.length" class="flex flex-col gap-2">
    <div class="px-1 text-xs text-n-slate-11">
      {{ visibleMatches.length }} possible duplicate{{
        visibleMatches.length === 1 ? '' : 's'
      }}
    </div>

    <div
      v-for="match in visibleMatches"
      :key="match.contact_id"
      class="flex flex-col gap-2 p-3 rounded-md bg-n-alpha-1"
    >
      <!-- Header: name + band label -->
      <div class="flex items-center justify-between gap-2">
        <span class="text-sm font-medium text-n-slate-12 truncate">
          {{ match.name || 'Unknown contact' }}
        </span>
        <span
          class="text-xs font-medium shrink-0"
          :class="labelColorClass(match.band)"
        >
          {{ bandLabel(match.band) }}
        </span>
      </div>

      <!-- Confidence meter -->
      <div class="flex items-center gap-2">
        <div class="flex-1 h-1.5 rounded-full bg-n-alpha-2 overflow-hidden">
          <div
            class="h-full rounded-full"
            :class="barColorClass(match.band)"
            :style="{ width: `${clampedScore(match.score)}%` }"
          />
        </div>
        <span class="text-xs tabular-nums text-n-slate-11 w-9 text-right">
          {{ clampedScore(match.score) }}%
        </span>
      </div>

      <!-- Evidence: what matched -->
      <div v-if="match.matched && match.matched.length" class="flex flex-col gap-1">
        <div
          v-for="(row, i) in match.matched"
          :key="`m-${i}`"
          class="flex items-center gap-1.5 text-xs text-n-slate-11"
        >
          <span class="i-lucide-check text-emerald-500 shrink-0" />
          <span class="font-medium">{{ row.label }}</span>
          <span v-if="row.detail" class="text-n-slate-10 truncate">
            {{ row.detail }}
          </span>
        </div>
        <!-- What did NOT match (only shown when clearly different) -->
        <div
          v-for="(row, i) in match.mismatched || []"
          :key="`x-${i}`"
          class="flex items-center gap-1.5 text-xs text-n-slate-10"
        >
          <span class="i-lucide-x text-n-slate-9 shrink-0" />
          <span>{{ row.label }}</span>
        </div>
      </div>

      <!-- Caution note (phone-only / name-only) -->
      <div
        v-if="match.note"
        class="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400"
      >
        <span class="i-lucide-triangle-alert shrink-0 mt-0.5" />
        <span>{{ match.note }}</span>
      </div>

      <!-- Actions -->
      <div class="flex items-center gap-3 pt-0.5">
        <a
          :href="contactUrl(match.contact_id)"
          class="text-xs font-medium text-n-brand hover:underline"
        >
          View contact
          <span class="i-lucide-arrow-up-right align-middle" />
        </a>
        <button
          type="button"
          class="text-xs font-medium text-n-slate-11 hover:text-n-slate-12 disabled:opacity-50 ml-auto"
          :disabled="!currentContactId || mergingId === match.contact_id"
          @click="onMerge(match)"
        >
          <span class="i-ph-arrows-merge align-middle" />
          {{ mergingId === match.contact_id ? 'Merging…' : 'Merge' }}
        </button>
      </div>
    </div>
  </div>
</template>
