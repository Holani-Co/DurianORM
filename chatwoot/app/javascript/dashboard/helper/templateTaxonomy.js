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

// Suggested categories for Instagram + Facebook (shared — same intents on both
// platforms). Values carry the surface prefix (dm_ / comment_) so the guided
// builder produces codes like `instagram_dm_price_furniture`. Suggestions only:
// the team can type any category, and the YAML in zoho-bridge is the real
// source of truth.
const SOCIAL_CATEGORIES = [
  { value: 'dm_price_furniture', label: 'DM · Price – Furniture' },
  { value: 'dm_price_door', label: 'DM · Price – Door' },
  { value: 'dm_price_wardrobe', label: 'DM · Price – Wardrobe' },
  { value: 'dm_price_fhc', label: 'DM · Price – Full Home Customisation' },
  { value: 'dm_catalogue_furniture', label: 'DM · Catalogue – Furniture' },
  { value: 'dm_address', label: 'DM · Address / store enquiry' },
  { value: 'dm_contact_details', label: 'DM · Contact details' },
  { value: 'dm_appreciation', label: 'DM · Appreciation' },
  { value: 'dm_greeting', label: 'DM · Greeting' },
  { value: 'dm_recruitment', label: 'DM · Recruitment' },
  { value: 'dm_design_collab_lead', label: 'DM · Design/collab lead' },
  { value: 'dm_influencer_collab', label: 'DM · Influencer collaboration' },
  { value: 'dm_vendor_supplier_pitch', label: 'DM · Vendor / supplier pitch' },
  { value: 'dm_marketing_spam_pitch', label: 'DM · Marketing / SEO pitch' },
  { value: 'dm_product_exchange', label: 'DM · Product exchange' },
  { value: 'dm_fhc_intro', label: 'DM · FHC intro / modular kitchen' },
  { value: 'dm_complaint_info_needed', label: 'DM · Complaint – info needed' },
  { value: 'dm_fraud_concern', label: 'DM · Fraud / serious concern' },
  { value: 'comment_contest', label: 'Comment · Contest entry' },
  { value: 'comment_praise', label: 'Comment · Praise' },
  { value: 'comment_redirect_to_dm', label: 'Comment · Redirect to DM' },
  { value: 'comment_product_mention', label: 'Comment · Product mention' },
  { value: 'comment_intent_to_visit', label: 'Comment · Intent to visit' },
];

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
  // Instagram and Facebook now have SEPARATE template sets (instagram_* /
  // facebook_*), each split by surface (dm_* / comment_*). Wording is identical
  // across platforms except platform terms ("DM" vs "inbox"). Same suggested
  // categories for both — seeded by zoho-bridge/sync_social_templates.py.
  {
    id: 'instagram',
    label: 'Instagram',
    icon: 'i-ph-instagram-logo',
    categories: SOCIAL_CATEGORIES,
  },
  {
    id: 'facebook',
    label: 'Facebook',
    icon: 'i-ph-facebook-logo',
    categories: SOCIAL_CATEGORIES,
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
