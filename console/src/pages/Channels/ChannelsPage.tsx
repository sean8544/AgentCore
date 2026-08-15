import { Card, Typography } from 'antd';
import { useI18n } from '../../i18n';

const { Title, Paragraph } = Typography;

export default function ChannelsPage() {
  const { t } = useI18n();
  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Title level={3}>{t('menu.channels')}</Title>
        <Paragraph>{t('common.comingSoon')}</Paragraph>
      </Card>
    </div>
  );
}
