// Copyright (c) YugaByte, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
// in compliance with the License.  You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software distributed under the License
// is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
// or implied.  See the License for the specific language governing permissions and limitations
// under the License.
//

#ifndef ENT_SRC_YB_CDC_CDC_CONSUMER_PROXY_MANAGER_H
#define ENT_SRC_YB_CDC_CDC_CONSUMER_PROXY_MANAGER_H

#include <vector>
#include <memory>

#include "yb/util/locks.h"

namespace yb {
namespace rpc {

class ProxyCache;

} // namespace rpc

namespace cdc {

struct ProducerTabletInfo;
class ProducerEntryPB;
class CDCServiceProxy;

class CDCConsumerProxyManager {
 public:
  ~CDCConsumerProxyManager();
  explicit CDCConsumerProxyManager(rpc::ProxyCache* proxy_cache);
  cdc::CDCServiceProxy* GetProxy(const ProducerTabletInfo& producer_tablet_info);
  void UpdateProxies(const ProducerEntryPB& producer_entry_pb);
 private:
  rpc::ProxyCache* proxy_cache_;

  rw_spinlock proxies_mutex_;
  std::vector<std::unique_ptr<CDCServiceProxy>> proxies_;
};

} // namespace cdc
} // namespace yb

#endif // ENT_SRC_YB_CDC_CDC_CONSUMER_PROXY_MANAGER_H
