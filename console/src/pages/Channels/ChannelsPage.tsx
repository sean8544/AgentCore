import { ApartmentOutlined } from '@ant-design/icons';
import GoogleCard from '../../components/GoogleCard';
import GooglePageHeader from '../../components/GooglePageHeader';
import { useI18n } from '../../i18n';

export default function ChannelsPage() {
  const { t } = useI18n();
  return (
    <div>
      <GooglePageHeader
        icon={<ApartmentOutlined />}
        title={t('menu.channels')}
      />
      <GoogleCard>
        {t('common.comingSoon')}
      </GoogleCard>
    </div>
  );
}
