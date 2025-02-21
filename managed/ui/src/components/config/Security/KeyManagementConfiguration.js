// Copyright (c) YugaByte, Inc.

import React, { Component, Fragment } from 'react';
import { Row, Col } from 'react-bootstrap';
import { Field, Formik } from 'formik';
import { YBFormInput, YBButton, YBFormSelect, YBCheckBox } from '../../common/forms/fields';
import { YBLoadingCircleIcon } from '../../common/indicators';
import { getPromiseState } from 'utils/PromiseUtils';
import ListKeyManagementConfigurations from './ListKeyManagementConfigurations';
import * as Yup from 'yup';

import { regionsData } from '../../config/PublicCloud/views/providerRegionsData';

const kmsConfigTypes = [
  { value: 'SMARTKEY', label: 'Equinix SmartKey' },
  { value: 'AWS', label: 'AWS KMS' }
];

const awsRegionList = regionsData.map((region, index) => {
  return {
    value: region.destVpcRegion,
    label: region.destVpcRegion,
  };
});

class KeyManagementConfiguration extends Component {
  state = {
    listView: false,
    enabledIAMProfile: false,
  }

  componentDidMount() {
    this.props.fetchKMSConfigList()
      .then(response => {
        if (response.payload.data.length) {
          this.setState({ listView: true });
        }
      });
  }

  submitKMSForm = (values) => {
    const { fetchKMSConfigList, setKMSConfig } = this.props;
    const { kmsProvider } = values;
    if (kmsProvider && kmsProvider.value === 'AWS') {
      let data = {
        'AWS_REGION': values.region.value,
      };
      if (!this.state.enabledIAMProfile) {
        data['AWS_ACCESS_KEY_ID'] = values.accessKeyId;
        data['AWS_SECRET_ACCESS_KEY'] = values.secretKeyId;
      }

      setKMSConfig(kmsProvider.value, data)
        .then(() => {
          fetchKMSConfigList();
          this.setState({ listView: true });
        });
    } else {
      const data = {
        'base_url': values.apiUrl || 'api.amer.smartkey.io',
        'api_key': values.apiKey,
      };
      setKMSConfig('SMARTKEY', data)
        .then(() => {
          fetchKMSConfigList();
          this.setState({ listView: true });
        });
    }
  }

  getSmartKeyForm = () => {
    return (
      <Fragment>
        <Row className="config-provider-row" key={'url-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">API Url</div>
          </Col>
          <Col lg={7}>
            <Field name={"apiUrl"}
                component={YBFormInput}
                placeholder={"api.amer.smartkey.io"}
                className={"kube-provider-input-field"}/>
          </Col>
        </Row>
        <Row className="config-provider-row" key={'private-key-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">Secret API Key</div>
          </Col>
          <Col lg={7}>
            <Field name={"apiKey"}
                component={YBFormInput}
                className={"kube-provider-input-field"}/>
          </Col>
        </Row>
      </Fragment>
    );
  }

  getAWSForm = () => {
    return (
      <Fragment>
        <Row className="config-provider-row" key={'iam-enable-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">Use IAM Profile</div>
          </Col>
          <Col lg={7}>
            <Field name={"enableIAMProfile"}
                component={YBCheckBox}
                input={{
                  onChange: () => this.setState({enabledIAMProfile: !this.state.enabledIAMProfile})
                }}
                className={"kube-provider-input-field"}/>
          </Col>
        </Row>
        <Row className="config-provider-row" key={'access-key-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">Access Key Id</div>
          </Col>
          <Col lg={7}>
            <Field name={"accessKeyId"}
                component={YBFormInput}
                disabled={this.state.enabledIAMProfile}
                className={"kube-provider-input-field"}/>
          </Col>
        </Row>
        <Row className="config-provider-row" key={'secret-key-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">Secret Key Id</div>
          </Col>
          <Col lg={7}>
            <Field name="secretKeyId"
                component={YBFormInput}
                disabled={this.state.enabledIAMProfile}
                className={"kube-provider-input-field"}/>
          </Col>
        </Row>
        <Row className="config-provider-row" key={'region-field'}>
          <Col lg={3}>
            <div className="form-item-custom-label">Region</div>
          </Col>
          <Col lg={7}>
            <Field name="region" component={YBFormSelect}
              options={awsRegionList}
              className={"kube-provider-input-field"}/>
          </Col>
        </Row>
      </Fragment>
    );
  }

  displayFormContent = (provider) => {
    if (!provider) {
      return this.getSmartKeyForm();
    }
    switch(provider.value) {
      case 'SMARTKEY':
        return this.getSmartKeyForm();
      case 'AWS':
        return this.getAWSForm();
      default:
        return this.getSmartKeyForm();
    }
  }

  openCreateConfigForm = () => {
    this.setState({ listView: false });
  }

  deleteAuthConfig = (provider) => {
    const { configList, deleteKMSConfig, fetchKMSConfigList } = this.props;
    deleteKMSConfig(provider);
    if (configList.data.length <= 1) {
      this.setState({ listView: false });
    } else {
      fetchKMSConfigList();
    }
  }

  render() {
    const { configList } = this.props;
    const { listView, enabledIAMProfile } = this.state;
    if (getPromiseState(configList).isInit() || getPromiseState(configList).isLoading()) {
      return <YBLoadingCircleIcon />;
    }
    if (listView) {
      return (
        <ListKeyManagementConfigurations
          configs={configList}
          onCreate={this.openCreateConfigForm}
          onDelete={this.deleteAuthConfig}
        />
      );
    }


    const validationSchema = Yup.object().shape({
      apiUrl: Yup.string(),

      apiKey: Yup.mixed()
        .when('kmsProvider', {
          is: provider => provider.value === 'SMARTKEY',
          then: Yup.mixed().required('API key is Required')
        }),

      accessKeyId: Yup.string()
        .when('kmsProvider', {
          is: provider => provider.value === 'AWS' && !enabledIAMProfile,
          then: Yup.string().required('Access Key ID is Required')
        }),

      secretKeyId: Yup.string()
        .when('kmsProvider', {
          is: provider => provider.value === 'AWS' && !enabledIAMProfile,
          then: Yup.string().required('Secret Key ID is Required')
        }),
      region: Yup.mixed()
        .when('kmsProvider', {
          is: provider => provider.value === 'AWS',
          then: Yup.mixed().required('AWS Region is Required')
        }),
    });

    return (
      <div className="provider-config-container">
        <Formik
          validationSchema={validationSchema}
          onSubmit={values => {
            this.submitKMSForm(values);
          }}
          render={props => (
            <form onSubmit={props.handleSubmit}>
              <Row>
                <Col lg={8}>
                  <Row className="config-provider-row" key={'provider-field'}>
                    <Col lg={3}>
                      <div className="form-item-custom-label">KMS Provider</div>
                    </Col>
                    <Col lg={7}>
                      <Field name="kmsProvider" placeholder="Provider name"
                          component={YBFormSelect}
                          options={kmsConfigTypes}
                          className={"kube-provider-input-field"}/>
                    </Col>
                  </Row>
                  {this.displayFormContent(props.values.kmsProvider)}
                </Col>
              </Row>
              <div className="form-action-button-container">
                <YBButton btnText={"Save"} btnClass={"btn btn-orange"}
                          disabled={ false } btnType="submit"/>
              </div>
            </form>
          )}
        />
      </div>
    );
  }
}
export default KeyManagementConfiguration;
