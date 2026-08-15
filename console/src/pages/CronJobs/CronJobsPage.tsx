import { Card, Typography } from 'antd';
import { useI18n } from '../../i18n';

const { Title, Paragraph } = Typography;

export default function CronJobsPage() {
  const { t } = useI18n();
  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Title level={3}>{t('menu.cronJobs')}</Title>
        <Paragraph>{t('common.comingSoon')}</Paragraph>
      </Card>
    </div>
  );
}
