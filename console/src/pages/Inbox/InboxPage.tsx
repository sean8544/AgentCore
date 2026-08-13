import { Card, Typography } from 'antd';

const { Title, Paragraph } = Typography;

export default function InboxPage() {
  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Title level={3}>收件箱</Title>
        <Paragraph>功能开发中，敬请期待。</Paragraph>
      </Card>
    </div>
  );
}
