// Durian — reply-template taxonomy.
//
// Reply templates are stored as Chatwoot canned responses whose short_code
// follows the convention `<channel>_<category>`, e.g. `review_positive_5star`.
// This is the single source of truth for:
//   - grouping templates by channel on the Canned Responses list
//   - the guided "name builder" on the create form
//   - (later) the bridge filtering templates per channel for AI replies
//
// Adding a channel = one entry in TEMPLATE_CHANNELS. Categories are only
// *suggestions* — the builder lets the team type a new one, so no code change
// is needed to introduce a new template situation.

// Bucket for canned responses that don't follow the channel convention
// (e.g. legacy `greeting`, `refund_policy`). Never hidden — shown under "Other".
export const OTHER_CHANNEL = 'other';

// id MUST match the short_code prefix. label/icon are display-only.
export const TEMPLATE_CHANNELS = [
  {
    id: 'review',
    label: 'Google Reviews',
    icon: 'i-ph-star',
    // Suggested categories (label shown in the combobox, value goes in the code).
    categories: [
      { value: 'positive_5star', label: 'Positive – 5★' },
      { value: 'positive_can_improve', label: 'Positive – can improve' },
      { value: 'negative_info_needed', label: 'Negative – info needed' },
      {
        value: 'negative_will_work_on_it',
        label: 'Negative – will work on it',
      },
      { value: 'issue_resolved', label: 'Issue resolved' },
      { value: 'issue_not_resolved', label: 'Issue not resolved' },
      { value: 'acknowledge_feedback', label: 'Acknowledge feedback' },
      { value: 'resolved_negative', label: 'Resolved negative' },
    ],
  },
  {
    // Instagram + Facebook DMs share one template set — Durian's own sheet
    // labels almost every template "FB & IG DM", so the wording is identical
    // across both. Seeded by zoho-bridge/setup_social_templates.py.
    id: 'social',
    label: 'Instagram / Facebook',
    icon: 'i-ph-chats-circle',
    categories: [
      { value: 'price_furniture', label: 'Price – Furniture' },
      { value: 'price_door', label: 'Price – Door' },
      { value: 'price_wardrobe', label: 'Price – Wardrobe' },
      { value: 'price_fhc', label: 'Price – Full Home Customisation' },
      { value: 'catalogue_door', label: 'Catalogue – Door' },
      { value: 'catalogue_wardrobe', label: 'Catalogue – Wardrobe' },
      { value: 'catalogue_furniture', label: 'Catalogue – Furniture' },
      { value: 'catalogue_fhc', label: 'Catalogue – Full Home Customisation' },
      { value: 'address', label: 'Address / store enquiry' },
      { value: 'address_door', label: 'Address – door (limited cities)' },
      { value: 'address_fhc', label: 'Address – FHC studio' },
      { value: 'contact_shared_ack', label: 'Contact shared – acknowledge' },
      { value: 'contact_details', label: 'Contact details' },
      { value: 'appreciation_5star', label: 'Appreciation – 5★' },
      { value: 'complaint_info_needed', label: 'Complaint – info needed' },
      { value: 'complaint_phone_shared', label: 'Complaint – phone shared' },
      { value: 'fraud_concern', label: 'Fraud / serious concern' },
      { value: 'product_exchange', label: 'Product exchange' },
      { value: 'ready_stock', label: 'Ready stock enquiry' },
      { value: 'fhc_intro', label: 'FHC intro / modular kitchen' },
      { value: 'expensive_fhc', label: 'Pricing objection – FHC' },
      { value: 'greeting', label: 'Greeting' },
      { value: 'recruitment', label: 'Recruitment' },
      { value: 'collaboration', label: 'Collaboration' },
      { value: 'comment_redirect_to_dm', label: 'Comment – redirect to DM' },
      { value: 'comment_praise', label: 'Comment – praise' },
      { value: 'comment_product_mention', label: 'Comment – product mention' },
      { value: 'comment_intent_to_visit', label: 'Comment – intent to visit' },
    ],
  },
  {
    id: 'whatsapp',
    label: 'WhatsApp',
    icon: 'i-ph-whatsapp-logo',
    categories: [],
  },
];

const CHANNEL_IDS = TEMPLATE_CHANNELS.map(c => c.id);

/**
 * Normalise a free-typed category into a short_code-safe slug.
 * "Negative – info needed" → "negative_info_needed"
 */
export const slugifyCategory = (raw = '') =>
  raw
    .toString()
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');

/**
 * Parse a short_code into { channel, category }. Unknown prefixes (legacy
 * canned responses) map to the "other" channel with the whole code as category.
 */
export const parseShortCode = (shortCode = '') => {
  const idx = shortCode.indexOf('_');
  const prefix = idx === -1 ? shortCode : shortCode.slice(0, idx);
  if (idx === -1 || !CHANNEL_IDS.includes(prefix)) {
    return { channel: OTHER_CHANNEL, category: shortCode };
  }
  return { channel: prefix, category: shortCode.slice(idx + 1) };
};

/** Assemble a short_code from a channel id + (raw or slugged) category. */
export const buildShortCode = (channel, category) => {
  const slug = slugifyCategory(category);
  return slug ? `${channel}_${slug}` : channel;
};

/** The channel descriptor for an id (falls back to a synthetic "Other"). */
export const channelMeta = channelId =>
  TEMPLATE_CHANNELS.find(c => c.id === channelId) || {
    id: OTHER_CHANNEL,
    label: 'Other',
    icon: 'i-ph-folder',
    categories: [],
  };
