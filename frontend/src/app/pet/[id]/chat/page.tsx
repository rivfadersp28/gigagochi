import { ChatView } from "@/components/ChatView";

type ChatPageProps = {
  params: Promise<{ id: string }>;
};

export default async function ChatPage({ params }: ChatPageProps) {
  const { id } = await params;

  return <ChatView petId={id} />;
}
