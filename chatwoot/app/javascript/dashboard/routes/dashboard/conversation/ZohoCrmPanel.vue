<script setup>
// Durian — CRM sidebar panel. Renders alongside the Zoho Desk panel.
//
// Reads three ids stashed on the conversation's custom_attributes by the
// bridge:
//   crm_contact_id — set by the auto path (Phase A) when a sales-shaped
//                    email lands (product/general/existing-order).
//   crm_lead_id    — set when the agent clicks "Create Lead" here.
//   crm_deal_id    — set when the agent clicks "Create Deal" here.
//
// Buttons are category-gated (the agent doesn't see "Create Deal" on a
// legal complaint). If an id is already set, the button becomes a
// "View in Zoho CRM" link so a re-click can't create a duplicate.
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
const leadId    = computed(() => String(props.customAttributes.crm_lead_id    || ''));
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

// Same lists as ZOHO_CRM_LEAD_CATEGORIES / ZOHO_CRM_DEAL_CATEGORIES in
// config.py — kept in sync so the UI matches what the bridge will accept.
const LEAD_CATEGORIES = new Set([
  'product_enquiry',
  'general_information',
  'existing_order_enquiry',
]);
const DEAL_CATEGORIES = new Set([
  'project_bulk_order',
  'doors_veneer_plywood',
  'product_enquiry',
  'general_information',
  'existing_order_enquiry',
]);

const showLead = computed(() => LEAD_CATEGORIES.has(categoryKey.value));
const showDeal = computed(() => DEAL_CATEGORIES.has(categoryKey.value));

const isCreatingLead = ref(false);
const isCreatingDeal = ref(false);
const confirmLeadDialog = ref(null);
const confirmDealDialog = ref(null);

// Build the CRM deep-link URL from the id — mirrors zoho_crm.contact_url /
// lead_url / deal_url server-side (kept simple: prod URL only, sandbox users
// get a slightly wrong link but the id is still correct).
const contactUrl = computed(() =>
  contactId.value ? `https://crm.zoho.in/crm/tab/Contacts/${contactId.value}` : ''
);
const leadUrl = computed(() =>
  leadId.value ? `https://crm.zoho.in/crm/tab/Leads/${leadId.value}` : ''
);
const dealUrl = computed(() =>
  dealId.value ? `https://crm.zoho.in/crm/tab/Potentials/${dealId.value}` : ''
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

const createLead = async () => {
  if (isCreatingLead.value) return;
  isCreatingLead.value = true;
  try {
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/create_crm_lead`,
      { conversation_id: Number(props.conversationId) }
    );
    if (data?.lead_id) {
      mergeConversationCustomAttributes({ crm_lead_id: data.lead_id });
      useAlert(data.duplicate
        ? 'Reused existing Lead in Zoho CRM.'
        : 'Lead created in Zoho CRM.');
    } else if (data?.dry_run) {
      useAlert('CRM is in dry-run mode — no Lead was created.');
    }
  } catch (e) {
    useAlert(
      e?.response?.data?.detail ||
      e?.response?.data?.error ||
      'Could not create the Lead. Check the bridge logs.'
    );
  } finally {
    isCreatingLead.value = false;
    confirmLeadDialog.value?.close();
  }
};

const createDeal = async () => {
  if (isCreatingDeal.value) return;
  isCreatingDeal.value = true;
  try {
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/zoho_bridge/create_crm_deal`,
      { conversation_id: Number(props.conversationId) }
    );
    if (data?.deal_id) {
      const updates = { crm_deal_id: data.deal_id };
      // The Deal creation may have also created a Contact — surface it too.
      if (!contactId.value && data?.contact_id) {
        updates.crm_contact_id = data.contact_id;
      }
      mergeConversationCustomAttributes(updates);
      useAlert('Deal created in Zoho CRM.');
    } else if (data?.dry_run) {
      useAlert('CRM is in dry-run mode — no Deal was created.');
    }
  } catch (e) {
    useAlert(
      e?.response?.data?.detail ||
      e?.response?.data?.error ||
      'Could not create the Deal. Check the bridge logs.'
    );
  } finally {
    isCreatingDeal.value = false;
    confirmDealDialog.value?.close();
  }
};

const requestCreateLead = () => confirmLeadDialog.value?.open();
const requestCreateDeal = () => confirmDealDialog.value?.open();
</script>

<template>
  <div class="flex flex-col gap-2 py-1 text-sm">
    <!-- Contact row: shown once the auto path (Phase A) has linked one. -->
    <div v-if="contactId" class="flex items-center gap-1.5 text-n-slate-11">
      <span class="i-lucide-user-round text-n-slate-10" />
      <span>Contact:</span>
      <a
        :href="contactUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-n-brand hover:underline truncate"
      >
        {{ contactId }}
      </a>
    </div>
    <div v-else class="text-xs text-n-slate-10">
      No CRM Contact linked yet. One will be created on the first qualifying
      enquiry, or when you create a Deal below.
    </div>

    <!-- Lead row -->
    <div v-if="leadId" class="flex items-center gap-1.5 text-n-slate-11">
      <span class="i-lucide-star text-n-slate-10" />
      <span>Lead:</span>
      <a
        :href="leadUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-n-brand hover:underline truncate"
      >
        {{ leadId }}
      </a>
    </div>

    <!-- Deal row -->
    <div v-if="dealId" class="flex items-center gap-1.5 text-n-slate-11">
      <span class="i-lucide-target text-n-slate-10" />
      <span>Deal:</span>
      <a
        :href="dealUrl"
        target="_blank"
        rel="noopener noreferrer"
        class="text-n-brand hover:underline truncate"
      >
        {{ dealId }}
      </a>
    </div>

    <!-- Buttons — category-gated, disabled once the record exists. -->
    <div v-if="showLead || showDeal" class="flex flex-wrap items-center gap-2 pt-1">
      <button
        v-if="showLead"
        type="button"
        class="px-2.5 py-1 text-xs font-medium rounded-md text-white bg-n-brand hover:opacity-90 disabled:opacity-50"
        :disabled="isCreatingLead || !!leadId"
        @click="requestCreateLead"
      >
        <span class="i-lucide-plus align-middle" />
        {{ leadId ? 'Lead already created' : (isCreatingLead ? 'Creating…' : 'Create Lead') }}
      </button>
      <button
        v-if="showDeal"
        type="button"
        class="px-2.5 py-1 text-xs font-medium rounded-md bg-n-solid-3 text-n-slate-12 hover:bg-n-solid-2 disabled:opacity-50"
        :disabled="isCreatingDeal || !!dealId"
        @click="requestCreateDeal"
      >
        <span class="i-lucide-plus align-middle" />
        {{ dealId ? 'Deal already created' : (isCreatingDeal ? 'Creating…' : 'Create Deal') }}
      </button>
    </div>

    <!-- Confirmation dialogs — mirrors the AI-review-suggestion Send flow. -->
    <Dialog
      ref="confirmLeadDialog"
      type="alert"
      title="Create Lead in Zoho CRM?"
      description="This will create a new Lead record in Zoho CRM using this enquiry's details. Continue?"
      confirm-button-label="Create Lead"
      @confirm="createLead"
    />
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
