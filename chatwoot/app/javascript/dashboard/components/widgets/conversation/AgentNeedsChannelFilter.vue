<script setup>
// Channel picker shown only in the unified "Agent needs" view. Selecting a
// channel narrows the conversation list to that channel's per-channel label
// (agent-needed-<channel>); "All channels" shows the umbrella `agent-needed`.
// Presentational only — the parent (ChatList) owns the route navigation.
defineProps({
  channel: { type: String, default: '' }, // '' = all channels
});

const emit = defineEmits(['change']);

// `value` maps to the label suffix the zoho-bridge applies
// (agent-needed-<value>); '' → the umbrella agent-needed. Keep in sync with
// main.py's _AGENT_NEEDED_ALL.
const CHANNEL_OPTIONS = [
  { value: '', label: 'All channels' },
  { value: 'email', label: 'Email' },
  { value: 'instagram', label: 'Instagram' },
  { value: 'facebook', label: 'Facebook' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'reviews', label: 'Google Reviews' },
];

const onChange = e => emit('change', e.target.value);

const selectClass =
  'w-full min-w-0 px-2 py-1 text-sm rounded-md cursor-pointer bg-n-alpha-2 text-n-slate-12 border border-n-weak focus:outline-none focus:border-n-brand';
</script>

<template>
  <div class="px-3 py-2">
    <select :value="channel" :class="selectClass" @change="onChange">
      <option
        v-for="opt in CHANNEL_OPTIONS"
        :key="opt.value || 'all'"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
  </div>
</template>
