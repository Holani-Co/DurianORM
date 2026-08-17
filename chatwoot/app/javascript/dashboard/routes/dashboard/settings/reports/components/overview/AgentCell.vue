<script setup>
import { useRouter } from 'vue-router';
import BaseCell from 'dashboard/components/table/BaseCell.vue';
import Avatar from 'next/avatar/Avatar.vue';
import { useMapGetter } from 'dashboard/composables/store';

const props = defineProps({
  row: {
    type: Object,
    required: true,
  },
});

const router = useRouter();
const isRTL = useMapGetter('accounts/isRTL');
const accountId = useMapGetter('getCurrentAccountId');

// Deep-link to the conversation dashboard filtered to this agent's assigned
// conversations. ChatList reads the assignee_id query param on mount and
// applies the filter (assignee filters have no dedicated route like labels do).
const openAgentConversations = () => {
  const { id, agent } = props.row.original;
  if (!id) return;
  router.push({
    name: 'home',
    params: { accountId: accountId.value },
    query: { assignee_id: id, assignee_name: agent },
  });
};
</script>

<template>
  <BaseCell>
    <button
      type="button"
      class="items-center flex text-left w-full rounded-md p-1 -m-1 hover:bg-n-alpha-1"
      :class="{ 'flex-row-reverse': isRTL }"
      @click="openAgentConversations"
    >
      <Avatar
        :src="row.original.thumbnail"
        :name="row.original.agent"
        :status="row.original.status"
        :size="32"
        hide-offline-status
        rounded-full
      />
      <div class="items-start flex flex-col min-w-0 my-0 mx-2">
        <h6
          class="overflow-hidden text-sm m-0 leading-[1.2] text-n-slate-12 whitespace-nowrap text-ellipsis"
        >
          {{ row.original.agent }}
        </h6>
        <span class="text-xs text-n-slate-11">
          {{ row.original.email }}
        </span>
      </div>
    </button>
  </BaseCell>
</template>
