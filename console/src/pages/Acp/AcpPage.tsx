import { Typography } from 'antd';
import { useI18n } from '../../i18n';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';

const { Paragraph } = Typography;

export default function AcpPage() {
  const { t } = useI18n();
  return (
    <div>
      <GooglePageHeader title="ACP" subtitle={t('common.comingSoon')} />
      <GoogleCard>
        <Paragraph>{t('common.comingSoon')}</Paragraph>
      </GoogleCard>
    </div>
  );
}
