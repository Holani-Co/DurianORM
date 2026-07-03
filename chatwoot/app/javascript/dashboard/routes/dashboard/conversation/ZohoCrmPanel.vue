<script setup>
// Durian — CRM sidebar panel. Renders alongside the Zoho Desk panel.
//
// Reads two ids stashed on the conversation's custom_attributes by the
// bridge:
//   crm_contact_id — set by the auto path when a sales-shaped email lands
//                    (product/general/existing-order) with a resolvable
//                    location.
//   crm_deal_id    — set when the agent clicks "Create Deal" here (the
//                    "human approves deal" step of the qualification flow).
//
// No "Create Lead": the client treats Leads and Deals as the same thing.
// The button is category-gated (the agent doesn't see "Create Deal" on a
// legal complaint) and disables once the Deal exists so a re-click can't
// create a duplicate.
import { computed, ref } from 'vue';
import { useStore, useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';
import Dialog from 'dashboard/components-next/dialog/Dialog.vue';

const axios = window.axios;
const store = useStore();
const accountId = useMapGetter('getCurrentAccountId');

const props = defineProps({
  conversationId: {
    type: [Number, String],
    required: true,
  },
  customAttributes: {
    type: Object,
    default: () => ({}),
  },
});

// Contact / Lead / Deal ids — reactive to store updates when the bridge
// merges custom_attributes after a successful create.
const contactId = computed(() => String(props.customAttributes.crm_contact_id || ''));
const dealId    = computed(() => String(props.customAttributes.crm_deal_id    || ''));

// Category — drives which buttons show. Try email_category_v2 first (auto
// path writes the full classifier result there); fall back to phase2_category
// (the plain string _phase2_execute_actions writes on BOTH auto AND agent-
// confirmed paths). Otherwise a conversation resolved via the decision card
// wouldn't show the Create buttons.
const categoryKey = computed(() => {
  const attrs = props.customAttributes || {};
  return (attrs.email_category_v2 || {}).category
      || attrs.phase2_category
      || '';
});

// Same list as ZOHO_CRM_DEAL_CATEGORIES in config.py — kept in sync so the
// UI matches what the bridge will accept. No "Create Lead": the client treats
// Leads and Deals as the same thing, so Deal is the only manual CRM action.
// General Information / Existing Order Enquiry are informational (not sales
// opportunities) and Franchise / Vendor are non-customer, so none of them
// offer a Deal — the button only shows for genuine sales categories.
const DEAL_CATEGORIES = new Set([
  'project_bulk_order',
  'doors_veneer_plywood',
  'full_home_customization',
  'product_enquiry',
]);

const showDeal = computed(() => DEAL_CATEGORIES.has(categoryKey.value));

const isCreatingDeal = ref(false);
const confirmDealDialog = ref(null);
// When the bridge can't confidently classify the buyer type (government vs
// private), it returns 409 and the agent decides here — matching the flow's
// "Govt / CPWD?" decision diamond.
const sectorChoiceNeeded = ref(false);

// CRM deep links — prefer the server-derived URLs the bridge stashes
// alongside the ids (crm_contact_url / crm_deal_url): only the bridge knows
// which Zoho data center the CRM org lives on (.com vs .in). The .in pattern
// is a legacy fallback for records linked before URLs were stored.
const contactUrl = computed(
  () =>
    props.customAttributes.crm_contact_url ||
    (contactId.value
      ? `https://crm.zoho.in/crm/tab/Contacts/${contactId.value}`
      : '')
);
const dealUrl = computed(
  () =>
    props.customAttributes.crm_deal_url ||
    (dealId.value
      ? `https://crm.zoho.in/crm/tab/Potentials/${dealId.value}`
      : '')
);

// After a successful create, patch the local conversation record so the
// panel reflects the new id without waiting for a page refresh. The store
// mutation does the merge itself — just pass the diff.
const mergeConversationCustomAttributes = attrs => {
  store.commit('UPDATE_CONVERSATION_CUSTOM_ATTRIBUTES', {
    conversationId: Number(props.conversationId),
    customAttributes: attrs,
  });
};

const createDeal = async (sector = '') => {
  if (isCreatingDeal.value) return;
  isCreatingDeal.value = true;
  try {
    const payload = { conversation_id: Number(props.conversationId) };
    if (sector) payload.sector = sector;
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/create_crm_deal`,
      payload
    );
    if (data?.deal_id) {
      const updates = { crm_deal_id: data.deal_id };
      // The Deal creation may have also created a Contact — surface it too.
      if (!contactId.value && data?.contact_id) {
        updates.crm_contact_id = data.contact_id;
      }
      mergeConversationCustomAttributes(updates);
      sectorChoiceNeeded.value = false;
      useAlert('Deal created in Zoho CRM.');
    } else if (data?.dry_run) {
      useAlert('CRM is in dry-run mode — no Deal was created.');
    }
  } catch (e) {
    if (e?.response?.status === 409) {
      // Buyer type ambiguous — surface the Government/Private choice.
      sectorChoiceNeeded.value = true;
      useAlert('Buyer type unclear — choose Government or Private below.');
    } else {
      useAlert(
        e?.response?.data?.detail ||
        e?.response?.data?.error ||
        'Could not create the Deal. Check the bridge logs.'
      );
    }
  } finally {
    isCreatingDeal.value = false;
    confirmDealDialog.value?.close();
  }
};

const requestCreateDeal = () => confirmDealDialog.value?.open();
</script>

<template>
  <div class="flex flex-col gap-2 py-1 text-sm">
    <!-- Contact row: shown once the auto path (Phase A) has linked one.
         Friendly link text — the raw Zoho record id is meaningless to agents,
         so it lives only inside the href. -->
    <div v-if="contactId" class="flex items-center gap-1.5 text-n-slate-11">
      <span class="i-lucide-user-round text-n-slate-10" />
      <span>Contact linked</span>
      <a
        :href="contactUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-n-brand hover:underline"
      >
        View in CRM ↗
      </a>
    </div>
    <div v-else class="text-xs text-n-slate-10">
      No CRM Contact linked yet. One will be created on the first qualifying
      enquiry, or when you create a Deal below.
    </div>

    <!-- Deal row -->
    <div v-if="dealId" class="flex items-center gap-1.5 text-n-slate-11">
      <span class="i-lucide-target text-n-slate-10" />
      <span>Deal created</span>
      <a
        :href="dealUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-n-brand hover:underline"
      >
        View in CRM ↗
      </a>
    </div>

    <!-- Create Deal — category-gated, disabled once the Deal exists. The
         click is the "human approves deal" step of the qualification flow. -->
    <div v-if="showDeal" class="flex flex-wrap items-center gap-2 pt-1">
      <button
        type="button"
        class="px-2.5 py-1 text-xs font-medium rounded-md text-white bg-n-brand hover:opacity-90 disabled:opacity-50"
        :disabled="isCreatingDeal || !!dealId"
        @click="requestCreateDeal"
      >
        <span class="i-lucide-plus align-middle" />
        {{ dealId ? 'Deal already created' : (isCreatingDeal ? 'Creating…' : 'Create Deal') }}
      </button>
    </div>

    <!-- Buyer-type decision — shown when the bridge couldn't confidently
         classify government vs private (the flow's "Govt / CPWD?" diamond).
         The agent's pick is sent as the sector and recorded on the
         conversation. -->
    <div
      v-if="sectorChoiceNeeded && !dealId"
      class="flex flex-col gap-1.5 p-2 border rounded-md border-n-weak bg-n-alpha-1"
    >
      <span class="text-xs text-n-slate-11">
        Buyer type unclear — who is this enquiry from?
      </span>
      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2 disabled:opacity-50"
          :disabled="isCreatingDeal"
          @click="createDeal('government')"
        >
          Government buyer
        </button>
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2 disabled:opacity-50"
          :disabled="isCreatingDeal"
          @click="createDeal('private')"
        >
          Private buyer
        </button>
        <button
          type="button"
          class="px-2 py-1 text-xs rounded-md text-n-slate-11 hover:text-n-slate-12"
          @click="sectorChoiceNeeded = false"
        >
          Cancel
        </button>
      </div>
    </div>

    <!-- Confirmation dialog — mirrors the AI-review-suggestion Send flow. -->
    <Dialog
      ref="confirmDealDialog"
      type="alert"
      title="Create Deal in Zoho CRM?"
      description="This will create a new Deal linked to the sender's Contact in Zoho CRM. Continue?"
      confirm-button-label="Create Deal"
      @confirm="createDeal"
    />
  </div>
</template>
